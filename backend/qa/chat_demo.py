"""
比赛用药咨询 demo：17药动态识别 + 通用问题银行检索 + 双模型受控转述。

默认链路：
用户问题 -> 药品识别 -> 剥离当前药名 -> embedding匹配意图 -> 药品卡片取标准答案
        -> Qwen3-0.6B受控说人话 -> 质检失败则回退标准答案。

运行：
  python chat_demo.py              # 受控LLM对话
  python chat_demo.py --raw        # 只输出卡片原文
  python chat_demo.py --self-test  # 检索结构测试
  python chat_demo.py --llm-smoke  # 少量LLM效果烟测
"""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

try:
    import requests
except ImportError:
    requests = None
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

from onnx_bge_encoder import OnnxBGEEncoder
from drug_name_resolver import (
    canonical_question as canonicalize_drug_question,
    find_latin_alias,
    fuzzy_candidate,
)

os.environ["HF_HUB_OFFLINE"] = "1"

BASE_DIR = Path(__file__).resolve().parent
BANK_PATH = BASE_DIR / "通用问题银行.json"
CARD_DIR = BASE_DIR / "药品知识卡片"
ACTIVE_DRUGS_PATH = BASE_DIR / "比赛药品清单.json"
INDICATION_COVERAGE_PATH = BASE_DIR / "药品适用范围词表.json"
CACHE_DIR = BASE_DIR / "cache"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
INDEX_CACHE_PATH = CACHE_DIR / "retrieval_index_bge_small_zh_v1_5.npz"
EMBEDDING_DIR = BASE_DIR / "embedding" / "bge-small-zh-v1.5"
EMBEDDING_ONNX_FP32 = EMBEDDING_DIR / "encoder_fp32.onnx"
EMBEDDING_ONNX_INT8 = EMBEDDING_DIR / "encoder_int8.onnx"
DEFAULT_EMBED_BACKEND = os.getenv("MEDICINE_QA_EMBED_BACKEND", "auto")

# PC only: localhost may be routed through the desktop proxy stack.  The
# loopback address reaches the same local Ollama service without that delay.
OLLAMA = "http://127.0.0.1:11434/api/generate"
OLLAMA_KEEP_ALIVE = "30m"
DEFAULT_MODEL = "qwen3:0.6b"
DEFAULT_LLM_BACKEND = os.getenv("MEDICINE_QA_LLM_BACKEND", "ollama")
DEFAULT_LLM_URL = os.getenv("MEDICINE_QA_LLM_URL", "")
UNLISTED_DRUG_NAMES = set()
LLM_OPTIONS = {
    # P7 is the production expression profile. High-risk and operation routes
    # still bypass the LLM completely.
    "temperature": 0.0,
    "top_p": 0.7,
    "top_k": 10,
    "min_p": 0.1,
    "num_ctx": 512,
    "num_predict": 88,
    "repeat_penalty": 1.05,
    "repeat_last_n": 64,
    "seed": 42,
}


def default_llm_url(backend):
    if DEFAULT_LLM_URL:
        return DEFAULT_LLM_URL
    if backend == "ollama":
        return OLLAMA
    if backend == "llama_cpp_completion":
        return "http://127.0.0.1:8080/completion"
    if backend == "llama_cpp_chat":
        return "http://127.0.0.1:8080/v1/chat/completions"
    return OLLAMA

MIN_MATCH_SCORE = 0.68
AMBIGUOUS_SCORE = 0.70
AMBIGUOUS_MARGIN = 0.03

# These symptoms require an urgent safety response even when the current
# medicine card does not have a dedicated alert unit.  The question-form guard
# keeps requests such as “有哪些危险信号” on the normal knowledge route.
GLOBAL_URGENT_PATTERNS = (
    ("呼吸困难", r"喘不上气|呼吸困难|无法呼吸|喉咙堵"),
    # "咽喉肿痛" is a common local symptom, not the emergency swelling signal.
    # Limit throat matching to explicit swelling/tightness/blocking language.
    ("面部或咽喉肿胀", r"(?:嘴唇|口唇|舌头|眼皮|脸|面部).{0,5}肿|(?:喉咙|咽喉)(?:肿胀|肿起来|肿大|肿了|发紧|堵)"),
    ("意识异常", r"晕厥|昏厥|晕过去|昏倒|意识不清|失去意识"),
    ("严重皮肤反应", r"水疱|水泡|皮肤.{0,6}(?:脱皮|剥脱)|全身.{0,6}(?:红疹|皮疹)"),
)


def global_urgent_signal(question):
    """Return a current urgent symptom label, or None for a hypothetical query."""
    query = re.sub(r"\s+", "", str(question or ""))
    if not query:
        return None
    # 孟鲁司特的“突然喘不上气能不能马上吃”是在问急性救援适用性。
    # 该药卡的 how_when 会明确说明它不是急救药并要求及时就医；应先让
    # 这个专属事实接管，避免全局分流掩盖“不能用于急性发作”的结论。
    if re.search(
        r"孟鲁司特.*(?:急性|急救|马上缓解|立刻缓解|突然喘|喘不上气.*(?:马上|急救)|胸闷.*(?:马上|急救))",
        query,
    ):
        return None
    hypothetical = ("有哪些", "有什么", "会不会", "可能吗", "什么情况", "什么表现")
    current_markers = (
        "我有", "我出现", "我现在", "我已经", "出现", "现在", "已经",
        "刚刚", "服后", "吃后", "用后", "发生",
    )
    if any(marker in query for marker in hypothetical) and not any(
        marker in query for marker in current_markers
    ):
        return None
    for label, pattern in GLOBAL_URGENT_PATTERNS:
        if re.search(pattern, query):
            return label
    return None


def global_urgent_answer(signal):
    return (
        f"如果已经出现{signal}，请立即停止自行服用可疑药物并尽快就医；"
        "呼吸困难或喉咙肿胀正在发生时，请立即呼叫急救。"
    )


def storage_damage_signal(question):
    """Recognize a stated damaged powder/granule without guessing a drug name."""
    query = re.sub(r"\s+", "", str(question or ""))
    has_form = bool(re.search(r"颗粒|散(?:剂)?|粉剂", query))
    has_damage = bool(re.search(r"受潮|结块|变色|异味|发霉|过期|变质|坏了", query))
    return has_form and has_damage


def generic_storage_damage_answer():
    return (
        "如果确认是颗粒剂、散剂或粉剂，受潮结块、变色、有异味、发霉或过期时不要再服用。"
        "请核对药名和药盒说明书，必要时咨询药师。"
    )


def is_suitable_population_query(question, card):
    """Recognize a generic population question without weakening CI facts."""
    query = clean_query(question, card).replace(" ", "")
    return bool(re.search(
        r"适用人群|哪些人(?:能|可以|适合)|什么人(?:能|可以|适合)|谁(?:能|可以)用|"
        r"适合什么人|人群有哪些",
        query,
    ))


def suitable_population_answer(card):
    """Aggregate only the card's reviewed special-population facts."""
    population_units = [
        (unit_id, unit)
        for unit_id, unit in card.get("units", {}).items()
        if unit_id.startswith("ci_")
    ]
    approved = [f"当前已核实的{card['drug_name']}特殊人群信息如下。"]
    for _, unit in population_units:
        approved.append(f"{unit.get('sub', '特殊人群')}：{unit.get('answer', '')}")
    approved.append("未列出的人群没有在这张知识卡中核实，不能据此判断是否适用；请以药盒说明书或医生、药师意见为准。")
    parts = plan_answer_parts(approved, ANSWER_PART_TARGET_CHARS)
    return parts, "\n".join(parts)


RISK_URGENT_UNITS = {
    "se_severe",
    "alert_severe",
    "alert_skin",
    "alert_gut",
    "od_action",
}
RISK_CAUTION_PREFIXES = ("reject_", "conflict_", "ci_", "stop_", "se_")
CONFLICT_ROUTE_UNITS = {
    "conflict_main", "conflict_nsaid", "conflict_antibiotic",
    "conflict_duplicate_antihistamine", "conflict_duplicate_antipyretic",
    "conflict_all", "conflict_tcm",
}
# 操作、限制和身份容易混淆的事实不适合由 0.6B 润色。直接使用已审核的
# 卡片答案，避免遗漏否定条件、把用户说成药品或擅自肯定可以自行用药。
DIRECT_SOURCE_UNITS = {
    "how_take", "how_mix", "how_timing", "how_often", "how_when", "how_note",
    "missed_how", "missed_warn", "od_action", "vd_dose", "storage_damage", "suitable_population", "se_urine_other_color", "ci_low_bp",
    "what_is", "not_cold", "tcm_type", "se_drowsy", "fd_alcohol", "fd_other",
    # P7 repeatedly omitted safety-critical details from these otherwise
    # low-risk units. Keep the reviewed source text intact instead.
    "se_common", "se_mild", "se_urine_yellow", "storage_cold",
}
TREATMENT_BOUNDARY_MARKERS = (
    "不是", "不能", "不建议", "不要", "需由医生", "需要由医生", "必须",
    "请按", "按医生", "说明书为准", "具体适用",
)


def is_fact_first_source(unit_id, answer):
    """Whether a selected fact must bypass the expression model."""
    if unit_id in DIRECT_SOURCE_UNITS or unit_id.startswith(("conflict_", "stop_")):
        return True
    if unit_id == "what_treat" and any(marker in answer for marker in TREATMENT_BOUNDARY_MARKERS):
        return True
    return False

# A Chinese character is a conservative approximation for one output token in
# this model.  Keep planned display parts well below the 88-token cap.
ANSWER_PART_TARGET_CHARS = 72

P7_PROMPT = """你只能利用“事实”，来输出关于“问题”的答案，答案需要口语化、个性化。"""
P7_OUTPUT_RULES = """回复只能是一到两句给用户看的完整中文。只输出答案正文，不得输出字段标签、内部指令、问题复述或作答说明；禁止以“按……回答：”“根据……回答：”“针对……回答：”“关于……：”“药名：”“问题：”“事实：”“答案：”开头。以“您”指代用户，药品只能用药品名或“该药”指代；不得把用户说成药品，不得把药品说成用户，不得无来源地说用户可以自行服用、使用、吃或用药。"""
P8_PROMPT = """你是封闭域药品FAQ的表达层，不是自由问答助手，也不负责判断药品、剂量或疾病。批准事实是唯一医学依据。请把批准事实组织成给用户看的自然中文，完整保留其中的药品名、数字、症状、条件、否定、禁忌、同服限制、停药和就医行动。允许调整语序、合并重复表达，并加入不含医学信息的礼貌衔接；不得新增、推断、删除或强化任何疗效、风险、因果、剂量、频次或建议。不要复述、改写或在开头重复用户问题；首句直接给出与批准事实一致的结论。只输出最终回答，不输出思考过程、字段名、问题、事实标签或协议文本。"""
PROMPT_PROFILES = {
    # The interactive P7 experiment also applied these output-hygiene rules.
    # Keep that exact composition in production so the benchmark remains valid.
    "P7": P7_PROMPT + "\n" + P7_OUTPUT_RULES,
    "P8": P8_PROMPT,
}
DEFAULT_PROMPT_VERSION = os.getenv("MEDICINE_QA_PROMPT_VERSION", "P7").upper()
if DEFAULT_PROMPT_VERSION not in PROMPT_PROFILES:
    DEFAULT_PROMPT_VERSION = "P7"
SYSTEM_PROMPT = PROMPT_PROFILES[DEFAULT_PROMPT_VERSION]

USER_PROMPT = """药品名称：{drug_name}
用户问题：{question}
表达要求：以“您”指代用户，以药品名称或“该药”指代药品；不得用“您”指代药品，也不得说用户是某种药。语气平和、耐心、简洁，通常用1到3句。不要复述或改写用户问题，不得无来源地肯定用户可以自行服药。
批准事实（只能使用以下内容）：
{answer}

请直接输出给用户看的回答："""

# 明确意图词只作为检索的补充证据；最终可用意图仍必须存在于当前药品卡片。
# 具体意图优先于泛化意图，避免“犯困”只被识别成笼统的副作用。
INTENT_HINT_PATTERNS = (
    ("se_drowsy", r"犯困|嗜睡|困倦|能不能开车|开车|骑车"),
    ("se_urine_other_color", r"(?:尿液|尿|小便).{0,6}(?:变绿|发绿|绿色|变红|发红|红色|褐色|棕色|深褐|酱油色)"),
    ("se_urine_yellow", r"尿黄|尿液发黄|小便黄|尿色"),
    ("se_rash", r"皮疹|红疹|过敏疹"),
    ("se_causality", r"是不是.*(?:药|吃药).*引起|是否.*(?:药|吃药).*导致|由.*药.*引起"),
    ("se_mild", r"(?:吃了|服后|用后).*(?:拉肚子|腹泻|不舒服|不适|恶心).*(?:怎么办|要不要停|能停吗)|(?:有点|轻微).*(?:拉肚子|腹泻|不舒服|不适|恶心).*怎么办|(?:肚子|胃).{0,4}(?:不舒服|疼|拉肚子|腹泻).*(?:怎么办|停药|能停)"),
    ("se_common", r"副作用|不良反应|有什么不舒服|不舒服吗"),
    ("alert_severe", r"危险信号|严重过敏|喘不上气|呼吸困难|嘴唇.*肿|眼皮.*肿"),
    ("alert_gut", r"腹泻.*严重|便血|血便|水样便|呕血"),
    ("ci_allergy", r"过敏.*能不能|过敏.*可以|过敏禁忌|对.*过敏|禁忌"),
    ("ci_pregnant", r"怀孕|孕期|孕妇"),
    ("ci_nursing", r"哺乳|喂奶"),
    ("ci_child", r"儿童|小孩|孩子"),
    ("ci_elderly", r"老人|老年人"),
    ("ci_organ", r"肝功能|肾功能|肝肾"),
    ("od_action", r"过量.*怎么办|吃多了.*怎么办|误服|服用过量"),
    ("od_symptoms", r"过量.*症状|吃多了.*症状"),
    ("missed_warn", r"(?:一次|今天|这次).*(?:两粒|两片|双倍|加倍|两份)|(?:双倍|加倍).*(?:补|吃|服)|补.*(?:两粒|两片|双倍|加倍)"),
    ("missed_how", r"漏服|漏吃|忘记吃|忘吃"),
    ("conflict_antibiotic", r"抗生素.*益生菌|益生菌.*抗生素"),
    ("conflict_tcm", r"中药|中成药|错开"),
    ("conflict_note", r"(?:和|同|一起).*(?:其他药|别的药).*(?:注意|禁忌)|(?:吃|服).*(?:其他药|别的药).*(?:注意|禁忌)|避免.*药物冲突"),
    ("ingredient_warn", r"成分|配方|里面有什么|复方.*含"),
    ("conflict_main", r"能和.*一起|能否同服|可以同服|一起吃|一起服|相互作用|药物冲突"),
    ("reject_no_effect", r"(?:吃|用).*(?:没好|不好|没效果|不管用)|怎么吃了还不好"),
    ("reject_diagnosis", r"(?:我这|这是).*(?:什么病|怎么了)|是不是.*(?:感染|疾病)|帮我.*(?:判断|诊断).*病情"),
    ("reject_unlisted_disease", r"(?:高血压|糖尿病|肿瘤|心脏病|肺炎|新冠|胃病).*(?:能治|能吃|可以吃)|能治.*(?:高血压|糖尿病|肿瘤|心脏病|肺炎|新冠|胃病)"),
    ("how_mix", r"怎么冲|如何冲|怎样冲|冲调|兑水"),
    ("how_often", r"一天.*(?:几次|几回|几顿)|每天.*(?:几次|几回)|多久.*一次|频次|次数"),
    ("how_timing", r"什么时候|何时|饭前|饭后|空腹|睡前|早上.*(?:吃|喝|用)|晚上.*(?:吃|喝|用)"),
    ("how_take", r"怎么(?:吃|服|用)|如何(?:吃|服|使用)|怎样(?:吃|服|使用)|服用方法|使用方法"),
    ("how_note", r"注意事项|需要注意|注意什么"),
    ("fd_alcohol", r"喝酒|饮酒|酒精|啤酒|白酒|红酒|能喝酒"),
    ("fd_other", r"牛奶|咖啡|浓茶|喝茶|饮食|忌口"),
    ("what_treat", r"功效|功能|功用|有什么效果|(?<!副)作用|用途|治什么|治疗什么|什么病|有什么用|干什么"),
    ("tcm_type", r"是不是所有.*都能|辨证|什么类型.*感冒|风寒|风热|寒包火"),
    ("not_cold", r"感冒.*吗|感冒.*能不能|感冒.*可以"),
    ("storage_cold", r"冷藏|冰箱|活菌"),
    ("storage_how", r"怎么保存|如何保存|保存方法|放在哪里"),
    ("storage_damage", r"变质|坏了|还能不能用"),
    ("stop_never", r"血压.*(?:正常|稳定).*(?:停|不吃)|降压.*(?:停|不吃)|血压好了.*(?:停|不吃)"),
    ("stop_rule", r"能不能停药|什么时候停药|停药"),
)

# “特殊身体状态 + 能否用药”属于安全决策，不允许向量检索把它错配到
# 普通功效或停药说明，再交给小模型猜测。只有当前药卡存在准确事实时，
# 才允许进入该事实；否则使用固定的未覆盖情境回复。
ELIGIBILITY_QUERY_PATTERN = re.compile(
    r"(?:能|可|应|该|要).{0,10}(?:吃|服|用|继续)|"
    r"(?:能否|可否|是否|适不适合|适合).{0,10}(?:吃|服|用|继续)?|"
    r"(?:行不行|可以吗|适合吗)"
)
# This is intentionally narrower than the generic eligibility expression.
# It authorizes a positive symptom-to-medicine match only when the user is
# asking whether a medicine can be used or can help for the named symptom.
TREATMENT_ELIGIBILITY_QUERY_PATTERN = re.compile(
    r"(?:能|可).{0,8}(?:吃|服|用|治|缓解)|"
    r"(?:可以|适合).{0,8}(?:吃|服|用|治|缓解)|"
    r"(?:能不能|可不可以|适不适合).{0,8}(?:吃|服|用|治|缓解)?"
)
CONDITION_ELIGIBILITY_RULES = (
    ("ci_low_bp", r"低血压|低压|血压(?:偏|很)?低", "低血压"),
    ("ci_low_glucose", r"低血糖|血糖(?:偏|很)?低", "低血糖"),
    ("ci_heart", r"心率(?:偏|很)?低|心动过缓|心衰|心功能不全|严重心脏病", "心血管异常"),
    ("ci_organ", r"肾功能|肾病|肾不好|肾衰|透析|肝功能|肝病|肝不好|肝衰", "肝肾功能异常"),
    ("ci_allergy", r"过敏体质|对.+过敏|过敏的人", "过敏史"),
    ("ci_pregnant", r"怀孕|孕期|孕妇|备孕", "孕期"),
    ("ci_nursing", r"哺乳|喂奶|母乳", "哺乳期"),
    ("ci_child", r"儿童|小孩|孩子|婴儿|宝宝", "儿童"),
    ("ci_elderly", r"老人|老年人|高龄", "高龄"),
    ("ci_stomach", r"胃溃疡|消化道出血|黑便", "消化道出血风险"),
)

PROTOCOL_MARKERS = (
    "<think>", "</think>", "<|answer|>", "<|im_start|>", "<|im_end|>",
    "problem_description", "status", "药名：", "问题：", "事实：",
)
INSTRUCTIONAL_PREFIX_PATTERN = re.compile(
    r"^\s*(?:(?:按|根据|针对).{0,24}(?:回答|回复|作答|说明)|"
    r"(?:关于|针对).{0,24}|(?:药名|药品|问题|事实|批准事实|答案))\s*[：:]"
)
SELF_MEDICATION_PATTERN = re.compile(
    r"(?:您|你)(?:可以|可|能)(?:自行|自己|直接)?(?:服用|使用|吃|用)"
)

RAW_CHATML_PROMPT = """<|im_start|>system
{system_prompt}
<|im_end|>
<|im_start|>user
{user_prompt}
<|im_end|>
<|im_start|>assistant
"""


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_data():
    bank = load_json(BANK_PATH)
    manifest = load_json(ACTIVE_DRUGS_PATH)
    indication_coverage = load_json(INDICATION_COVERAGE_PATH)
    global UNLISTED_DRUG_NAMES
    legacy_names = set()
    for item in manifest.get("legacy_cards", []):
        path = CARD_DIR / item.get("card_file", "")
        if not path.is_file():
            continue
        try:
            legacy_card = load_json(path)
        except (OSError, ValueError):
            continue
        legacy_names.update(all_names(legacy_card))
    UNLISTED_DRUG_NAMES = {name for name in legacy_names if name}
    active = manifest.get("active_drugs", [])
    if manifest.get("total_active_drugs") != len(active):
        raise RuntimeError("比赛药品清单的声明数量与实际数量不一致")
    if len(active) != 17:
        raise RuntimeError(f"比赛药品清单必须正好包含17种药，当前为{len(active)}种")

    cards = []
    names = set()
    files = set()
    for item in active:
        path = CARD_DIR / item["card_file"]
        if not path.is_file():
            raise RuntimeError(f"比赛药品卡片不存在：{path}")
        if path.name in files:
            raise RuntimeError(f"比赛药品清单重复引用卡片：{path.name}")
        card = load_json(path)
        if card.get("drug_name") != item.get("drug_name"):
            raise RuntimeError(
                f"药名与卡片不一致：清单={item.get('drug_name')}，卡片={card.get('drug_name')}"
            )
        if card["drug_name"] in names:
            raise RuntimeError(f"比赛药品清单药名重复：{card['drug_name']}")
        card["_file"] = path.name
        cards.append(card)
        names.add(card["drug_name"])
        files.add(path.name)
    if not cards:
        raise RuntimeError(f"没有找到药品卡片：{CARD_DIR}")
    if indication_coverage.get("schema_version") != 1:
        raise RuntimeError("药品适用范围词表版本不支持")
    coverage_cards = indication_coverage.get("cards")
    if not isinstance(coverage_cards, dict):
        raise RuntimeError("药品适用范围词表缺少 cards 对象")
    if set(coverage_cards) != names:
        missing = sorted(names - set(coverage_cards))
        unexpected = sorted(set(coverage_cards) - names)
        raise RuntimeError(f"药品适用范围词表与有效药卡不一致：缺少={missing}，多余={unexpected}")
    for card in cards:
        entries = coverage_cards[card["drug_name"]].get("approved_terms")
        if not isinstance(entries, list):
            raise RuntimeError(f"{card['drug_name']} 的 approved_terms 必须是数组")
        approved_terms = []
        seen_phrases = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError(f"{card['drug_name']} 的适用范围条目必须是对象")
            term = str(entry.get("term") or "").strip()
            source_unit = entry.get("source_unit")
            phrases = entry.get("phrases")
            if source_unit != "what_treat" or source_unit not in card["units"]:
                raise RuntimeError(f"{card['drug_name']} 的适用范围必须引用 what_treat")
            source_answer = str(card["units"][source_unit].get("answer") or "")
            if not term or term not in source_answer:
                raise RuntimeError(f"{card['drug_name']} 的适用范围术语未在知识卡 what_treat 原文中确认：{term}")
            if not isinstance(phrases, list) or not phrases:
                raise RuntimeError(f"{card['drug_name']} 的适用范围短语不能为空")
            normalized_phrases = []
            for phrase in phrases:
                normalized = re.sub(r"\s+", "", str(phrase or ""))
                if not normalized:
                    raise RuntimeError(f"{card['drug_name']} 的适用范围含空短语")
                if normalized in seen_phrases:
                    raise RuntimeError(f"{card['drug_name']} 的适用范围短语重复：{normalized}")
                seen_phrases.add(normalized)
                normalized_phrases.append(normalized)
            approved_terms.append({
                "term": term,
                "source_unit": source_unit,
                "phrases": tuple(normalized_phrases),
            })
        card["approved_indications"] = tuple(approved_terms)
    return bank, cards


def load_embedding_model(backend=DEFAULT_EMBED_BACKEND):
    selected = backend
    if selected == "auto":
        if EMBEDDING_ONNX_INT8.is_file():
            selected = "onnx-int8"
        elif SentenceTransformer is not None:
            selected = "sentence-transformers"
        elif EMBEDDING_ONNX_FP32.is_file():
            selected = "onnx-fp32"
        else:
            raise RuntimeError("没有可用的embedding后端：缺ONNX模型且未安装sentence-transformers")
    if selected == "sentence-transformers":
        if SentenceTransformer is None:
            raise RuntimeError("未安装sentence-transformers")
        emb = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
        emb.cache_key = "sentence_transformers_fp32"
        emb.backend_name = selected
        return emb
    if selected == "onnx-fp32":
        emb = OnnxBGEEncoder(EMBEDDING_ONNX_FP32)
    elif selected == "onnx-int8":
        emb = OnnxBGEEncoder(EMBEDDING_ONNX_INT8)
    else:
        raise ValueError(f"未知embedding后端：{selected}")
    emb.cache_key = selected.replace("-", "_")
    emb.backend_name = selected
    return emb


def index_cache_path_for(emb):
    cache_key = getattr(emb, "cache_key", "sentence_transformers_fp32")
    if cache_key == "sentence_transformers_fp32":
        return INDEX_CACHE_PATH
    return CACHE_DIR / f"retrieval_index_bge_small_zh_v1_5_{cache_key}.npz"


CARD_VARIANT_SCOPE_SEPARATOR = "\x1f"


def _index_fingerprint(cache_key, cards=None):
    digest = hashlib.sha256()
    digest.update(EMBEDDING_MODEL.encode("utf-8"))
    digest.update(cache_key.encode("utf-8"))
    digest.update(b"card_scoped_active_bank_variants_v2")
    digest.update(BANK_PATH.read_bytes())
    digest.update(ACTIVE_DRUGS_PATH.read_bytes())
    for card in cards or ():
        card_path = CARD_DIR / card.get("_file", "")
        if card_path.is_file():
            digest.update(card_path.read_bytes())
        else:
            digest.update(json.dumps(card, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def build_index(bank, emb, rebuild=False, cards=None):
    """Build a shared bank index plus card-scoped variants for active drugs."""
    variant_texts = []
    variant_unit_ids = []
    active_unit_ids = {
        unit_id
        for card in cards or ()
        for unit_id in card.get("units", {})
    }
    for unit_id, unit in bank["units"].items():
        # The bank contains legacy ibuprofen/NSAID intents.  Do not let those
        # vectors influence the active 17-drug retrieval set until a matching
        # medicine card is formally added to the active manifest.
        if unit_id not in active_unit_ids:
            continue
        for variant in unit["variants"]:
            variant_texts.append(variant)
            variant_unit_ids.append(unit_id)

    for card in cards or ():
        for unit_id, unit in card["units"].items():
            for variant in unit.get("variants", ()):
                normalized_variant = clean_query(variant, card)
                variant_texts.append(normalized_variant)
                variant_unit_ids.append(
                    f"{card['drug_name']}{CARD_VARIANT_SCOPE_SEPARATOR}{unit_id}"
                )

    cache_key = getattr(emb, "cache_key", "sentence_transformers_fp32")
    cache_path = index_cache_path_for(emb)
    fingerprint = _index_fingerprint(cache_key, cards)
    if not rebuild and cache_path.is_file():
        try:
            with np.load(cache_path, allow_pickle=False) as cached:
                cached_fingerprint = str(cached["fingerprint"].item())
                cached_texts = cached["variant_texts"].astype(str).tolist()
                cached_ids = cached["variant_unit_ids"].astype(str).tolist()
                cached_vecs = cached["variant_vecs"].astype(np.float32, copy=False)
            if (
                cached_fingerprint == fingerprint
                and cached_texts == variant_texts
                and cached_ids == variant_unit_ids
                and cached_vecs.ndim == 2
                and cached_vecs.shape[0] == len(variant_texts)
            ):
                return variant_texts, variant_unit_ids, cached_vecs, "cache"
        except (OSError, ValueError, KeyError):
            pass

    variant_vecs = emb.encode(
        variant_texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype(np.float32, copy=False)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        cache_path,
        fingerprint=np.asarray(fingerprint),
        variant_texts=np.asarray(variant_texts),
        variant_unit_ids=np.asarray(variant_unit_ids),
        variant_vecs=variant_vecs,
    )
    return variant_texts, variant_unit_ids, variant_vecs, "rebuilt"


def all_names(card):
    return [card["drug_name"], card["generic_name"]] + card.get("aliases", [])


def normalize_drug_message(text):
    """仅用于判断一条消息是否就是一个药名，不参与语义检索。"""
    return re.sub(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()\[\]【】]+", "", str(text or ""))


CONTEXT_REFERENCE_PATTERN = re.compile(
    r"这款药|这个药|这药|此药|该药|刚才那个药|上面那个药"
)
# Do not treat a Han character inside a medicine name as a pronoun.  For
# example, the "他" in "阿托伐他汀" must be allowed to trigger a new-drug
# refusal instead of silently inheriting the previous medicine context.
CONTEXT_PRONOUN_REFERENCE_PATTERN = re.compile(
    r"(^|[，。！？、；：,.!?;:\s])([它他她])(?=(?:的|有|能|可以|可不可以|怎么|如何|是|适合|"
    r"副作用|不良反应|功效|功能|功用|用途|作用|注意|保存|吃|服|用|喝))"
)
CONTEXT_PRONOUN_LEADIN_PATTERN = re.compile(
    r"(?:请问|想知道|想了解|告诉我|说说|帮我(?:看看|查查|说说))"
    r"([它他她])(?=(?:的|有|能|可以|可不可以|怎么|如何|是|适合|副作用|不良反应|"
    r"功效|功能|功用|用途|作用|注意|保存|吃|服|用|喝))"
)
CONTEXT_INTENT_ONLY_PATTERN = re.compile(
    r"(?:的)?(?:"
    r"(?:功效|功能|功用|用途|作用)(?:是什么|怎么样|有哪些)?|"
    r"(?:副作用|不良反应)(?:是什么|怎么样|有哪些)?|"
    r"有什么(?:用|作用|功效|效果|功能)|"
    r"怎么(?:吃|服|用)|如何(?:吃|服|用)|怎样(?:吃|服|用)|"
    r"(?:服用|使用)方法|一天(?:吃|服|用|喝)几次|"
    r"什么时候(?:吃|服|用)|注意(?:什么|事项)|怎么保存"
    r")"
)


def has_context_reference(question):
    """Whether wording explicitly points to the confirmed medicine in this session."""
    query = str(question or "")
    return bool(
        CONTEXT_REFERENCE_PATTERN.search(query)
        or CONTEXT_PRONOUN_REFERENCE_PATTERN.search(query)
        or CONTEXT_PRONOUN_LEADIN_PATTERN.search(query)
    )


def remove_context_references(question):
    """Remove only genuine follow-up references before intent-only matching."""
    query = CONTEXT_REFERENCE_PATTERN.sub("", str(question or ""))
    query = CONTEXT_PRONOUN_REFERENCE_PATTERN.sub(lambda match: match.group(1), query)
    return CONTEXT_PRONOUN_LEADIN_PATTERN.sub("", query)


def is_context_intent_only(question):
    """Recognize a bare follow-up such as “有什么用” without a medicine name."""
    query = normalize_drug_message(question)
    query = remove_context_references(query)
    query = query.lstrip("的")
    return bool(query) and bool(CONTEXT_INTENT_ONLY_PATTERN.fullmatch(query))


def is_drug_name_only(question, card, matched_name):
    """OCR 或手工输入只发药名时，进入该药的连续咨询上下文。"""
    if card is None or not matched_name:
        return False
    normalized_question = normalize_drug_message(question)
    return bool(normalized_question) and normalized_question in {
        normalize_drug_message(name) for name in all_names(card) if name
    }


def drug_context_greeting(card):
    return (
        f"已识别到药品“{card['drug_name']}”。"
        "请问您想了解它的功效、服用方法、副作用、注意事项，"
        "还是与其他药物能否同用？"
    )


def has_explicit_legacy_drug(question, cards):
    """Keep retired full names from being mistaken for an active substring alias."""
    query = str(question or "")
    active_hits = [
        name for card in cards for name in all_names(card)
        if name and name in query
    ]
    legacy_hits = [name for name in UNLISTED_DRUG_NAMES if name and name in query]
    return bool(legacy_hits) and (
        not active_hits
        or all(
            any(active_name in legacy_name for legacy_name in legacy_hits)
            for active_name in active_hits
        )
    )


def unlisted_drug_result():
    return {
        "card": None,
        "answer": "当前17种比赛药品知识库未收录这款药，不能沿用上一种药的答案。请仅咨询已收录的17种比赛药，或补充该药的知识卡后再问。",
        "answer_parts": [],
        "new_current_drug": None,
        "top3": [],
        "top_unit_id": None,
        "top_score": 0.0,
        "selected_unit_ids": [],
        "hinted_unit_ids": [],
        "embed_ms": 0.0,
        "llm_ms": 0.0,
        "risk_level": "normal",
        "fallback_reason": "unlisted_drug_reference",
        "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "done_reason": "not_called"},
    }


def looks_like_unlisted_drug(question, cards):
    """识别用户明确说出的、但不属于当前17药的药名，禁止继承上一轮药品。"""
    query = str(question or "")
    active_hits = [
        name for card in cards for name in all_names(card)
        if name and name in query
    ]
    legacy_hits = [name for name in UNLISTED_DRUG_NAMES if name and name in query]
    if legacy_hits and has_explicit_legacy_drug(question, cards):
        return True
    if active_hits:
        return False
    if has_context_reference(query) or is_context_intent_only(query):
        return False
    # “一次吃了两片”“忘了补一粒”是剂量/漏服语句，不能因为“片/粒”
    # 被正则当成一个新药名并阻断当前会话药品的过量处理。
    if re.search(
        r"(?:一次|每次|一天|每天|刚|误|吃了|服了|补|忘|漏).{0,8}"
        r"(?:[一二两三四五六七八九十\d]+)(?:片|粒|袋|滴)",
        query,
    ):
        return False
    return bool(re.search(
        r"[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:滴剂|胶囊|颗粒|喷雾剂|口服液|糖浆|软膏|注射液|片|散)",
        query,
    ))


def looks_like_unknown_drug_reference(question):
    """Detect a new, unrecognized drug reference so stale context is not reused."""
    query = re.sub(r"\s+", "", str(question or ""))
    if not query or has_context_reference(query) or is_context_intent_only(query):
        return False
    if re.search(r"[A-Za-z]{4,}", query):
        return True
    # An unknown medicine normally appears before the actual medicine question
    # (for example “阿托伐他汀怎么吃”).  Do not treat a bare intent such as
    # “功效是什么” as a medicine name just because it contains three Han chars.
    name_before_intent = re.match(
        r"([\u4e00-\u9fff]{2,18})(?:怎么(?:吃|用|保存)|如何(?:吃|用|保存)|"
        r"一天(?:吃|用|喝)|能不能(?:吃|用)|是什么药|有什么(?:用|作用)|"
        r"功效|副作用|不良反应|适用人群|治疗什么|能治什么|注意什么)",
        query,
    )
    if not name_before_intent:
        return False
    candidate = name_before_intent.group(1)
    return candidate not in {"这个", "这款", "此", "该", "刚才", "上面"}


def has_ambiguous_compound_cold_name(question):
    """Reject an incomplete compound cold-medicine reference."""
    query = re.sub(r"\s+", "", str(question or ""))
    return bool(re.search(r"复方感冒(?!灵|药)", query))


def is_medication_conflict_question(question, cards):
    """Require an actual co-medication relationship before conflict routing."""
    query = re.sub(r"\s+", "", str(question or ""))
    relation = re.search(
        r"能和|能与|和.{0,8}(?:一起|同服|同吃|合用)|与.{0,8}(?:一起|同服|同吃|合用)|"
        r"(?:一起|同时|同服|同吃|合用|配伍|相互作用|冲突|错开|间隔).{0,8}(?:吃|服|用)|"
        r"(?:吃|服|用).{0,8}(?:止痛药|退烧药|抗生素|益生菌|钙片|中成药|其他药|别的药)",
        query,
    )
    if not relation and re.search(r"还(?:能)?(?:吃|服|用)|再(?:吃|服|用)", query):
        relation = True
    if not relation:
        return False
    known_mentions = sum(
        any(name and name in query for name in all_names(card)) for card in cards
    )
    other_medicine = bool(re.search(
        r"其他药|别的药|抗生素|益生菌|钙片|退烧药|止痛药|中成药|布洛芬|对乙酰氨基酚|"
        r"西替利嗪|左西替利嗪|阿莫西林|多西环素",
        query,
    ))
    return known_mentions >= 2 or other_medicine


def find_drug(question, cards, current_drug=None):
    # 药品全名/通用名比“退烧药、降压药”这类别名更可靠。多药同问时，
    # 优先识别题目中明确说出的药名，避免“吃降压药能吃布洛芬吗”被
    # “降压药”这个别名抢走上下文。
    explicit_hits = []
    named_alias_hits = []
    generic_class_alias_hits = []
    # 这些是药物类别而非唯一药名，不能盖过题目中真正说出的药名。
    generic_class_aliases = {"退烧药", "止痛药", "降压药", "抗过敏药"}
    for card in cards:
        explicit_names = [card["drug_name"], card["generic_name"]]
        for name in explicit_names:
            if not name:
                continue
            idx = question.find(name)
            if idx >= 0:
                explicit_hits.append((idx, -len(name), card, name))
        for name in card.get("aliases", []):
            if not name or name in explicit_names:
                continue
            idx = question.find(name)
            if idx >= 0:
                target = generic_class_alias_hits if name in generic_class_aliases else named_alias_hits
                target.append((idx, -len(name), card, name))
    # “双歧杆菌能和阿莫西林一起吃吗”中，双歧杆菌虽是别名，却是位于
    # 句首的具体药名；它应优先于后面的阿莫西林。泛类别别名则最后考虑。
    hits = named_alias_hits + explicit_hits
    if not hits:
        hits = generic_class_alias_hits
    if hits:
        hits.sort(key=lambda item: (item[0], item[1]))
        return hits[0][2], hits[0][3]
    return current_drug, None


def clean_query(question, card):
    query = question
    for name in sorted(set(all_names(card)), key=len, reverse=True):
        query = re.sub(r"\s*" + re.escape(name) + r"\s*", "", query)
    return query.strip() or question


def is_unspecified_quality_query(question, card):
    """Reject vague quality judgments instead of inventing an efficacy scenario."""
    query = clean_query(question, card).replace(" ", "")
    return bool(re.fullmatch(r"(?:这个药|这药|它)?(?:好用|好不好用|管不管用|效果怎么样)(?:吗|呀)?", query))


def is_eligibility_query(question):
    """Whether the user asks to start/continue a medicine in a health state."""
    return bool(ELIGIBILITY_QUERY_PATTERN.search(str(question or "").replace(" ", "")))


def is_treatment_eligibility_query(question, card):
    """Whether wording asks if this medicine can be used for a named indication."""
    # Do not remove aliases here: an alias can also be a part of an indication,
    # such as "维生素D缺乏". Phrase matching is card-scoped, so the raw wording
    # has no cross-drug ambiguity at this stage.
    query = re.sub(r"\s+", "", str(question or ""))
    # "能治什么" is a general efficacy query, and opening/swallowing/mixing
    # questions are administration questions. Neither should enter the
    # symptom-to-medicine eligibility gate.
    if re.search(r"(?:能|可).{0,4}(?:治什么|治疗什么)|什么(?:病|症状)?.{0,4}(?:能治|能用)|(?:功效|作用|用途|管什么用)", query):
        return False
    if re.search(r"打开|掰开|掰碎|嚼碎|吞服|怎么吃|怎么用|怎么冲|如何冲|饭前|饭后|空腹|睡前", query):
        return False
    return bool(TREATMENT_ELIGIBILITY_QUERY_PATTERN.search(query))


def resolve_approved_indication(question, card, cards):
    """Return a reviewed what_treat route for an exact card-scoped symptom phrase."""
    if is_medication_conflict_question(question, cards):
        return None
    if not is_treatment_eligibility_query(question, card):
        return None
    query = re.sub(r"\s+", "", str(question or ""))
    for entry in card.get("approved_indications", ()):
        for phrase in entry["phrases"]:
            if phrase in query:
                return {
                    "unit_id": entry["source_unit"],
                    "term": entry["term"],
                    "phrase": phrase,
                }
    return None


def has_treatment_fact(card):
    """Whether the card contains a reviewed treatment/indication fact.

    The presence of this unit is enough to answer a treatment question from
    the card.  ``approved_indications`` is only an optional exact-term index;
    it must not become a second gate that hides the card's own fact.
    """
    answer = str(card.get("units", {}).get("what_treat", {}).get("answer") or "")
    return bool(answer.strip())


def has_guarded_treatment_fact(card):
    """Backward-compatible alias for older callers and regression scripts."""
    return has_treatment_fact(card)


def uncovered_indication_answer(card):
    return (
        f"当前{card['drug_name']}知识卡没有确认它可用于您提到的具体症状。"
        "不要自行套用；请核对药盒说明书，必要时咨询医生或药师。"
    )


def resolve_condition_eligibility(question, card):
    """Map an eligibility question to an exact card fact, or mark it uncovered."""
    if not is_eligibility_query(question):
        return None
    query = clean_query(question, card).replace(" ", "")
    for unit_id, pattern, condition_name in CONDITION_ELIGIBILITY_RULES:
        if re.search(pattern, query):
            return {
                "unit_id": unit_id if unit_id in card["units"] else None,
                "condition_name": condition_name,
            }
    return None


def uncovered_condition_answer(card):
    return (
        f"当前{card['drug_name']}知识卡没有确认这一特殊身体状态下是否适用，"
        "不能据此判断是否继续服用。请以手上药盒说明书为准，并咨询医生或药师。"
    )


def forced_intent(question, card):
    """高频、低歧义的服用问题不用依赖向量相似度。"""
    query = clean_query(question, card).replace(" ", "")
    units = card["units"]
    # Use the raw question here.  Cleaning a name such as “复方感冒灵颗粒”
    # would otherwise erase the dosage form before checking “受潮结块”.
    if storage_damage_signal(question) and "storage_damage" in units:
        return "storage_damage"
    if "vd_dose" in units and re.search(
        r"(?:几|多少|一天|每天).{0,4}滴|滴.{0,4}(?:几|多少)", query
    ):
        return "vd_dose"
    if "conflict_note" in units and re.search(
        r"(?:吃|服|用).{0,8}(?:之前|前).{0,8}(?:注意|其他药|同服)|"
        r"(?:之前|服前).{0,8}(?:注意|其他药|同服)", query,
    ):
        return "conflict_note"
    # This card explicitly distinguishes long-term control from acute rescue.
    # Resolve that domain-specific intent before the generic "喘不上气" alert,
    # whose emotional-warning fact would otherwise be unrelated.
    if (
        card["generic_name"] == "孟鲁司特钠"
        and "how_when" in units
        and re.search(r"急性|急救|马上缓解|立刻缓解|突然喘|喘不上气.*(?:马上|急救)|胸闷.*(?:马上|急救)", query)
    ):
        return "how_when"
    # 先处理危险症状。“一天拉好几次”里的“一天”不是服药频次，
    # 绝不能被下面的 how_often 规则覆盖。具体危险信号优先于笼统严重不适。
    if re.search(r"喘不上气|呼吸困难|不能呼吸|(?:面|脸|嘴唇|眼皮|舌头).{0,4}肿|(?:喉咙|咽喉)(?:肿胀|肿起来|肿大|肿了|发紧|堵)", query):
        if "alert_severe" in units:
            return "alert_severe"
    if re.search(r"水疱|水泡|皮肤.{0,6}(?:脱皮|脱落)|(?:大片|全身).{0,8}(?:红斑|红疹|皮疹)", query):
        if "alert_skin" in units:
            return "alert_skin"
    if re.search(r"(?:水样便|腹泻|拉肚子).{0,12}(?:发热|发烧|血便|便血|腹痛|肚子痛|脱水|虚脱)|(?:发热|发烧|血便|便血|脱水|虚脱).{0,12}(?:水样便|腹泻|拉肚子)", query):
        if "alert_gut" in units:
            return "alert_gut"
    if (
        "se_urine_other_color" in units
        and re.search(r"(?:尿液|尿|小便).{0,6}(?:变绿|发绿|绿色|变红|发红|红色|褐色|棕色|深褐|酱油色)", query)
    ):
        return "se_urine_other_color"
    severe_pattern = (
        r"水样便|血便|大便带血|呕血|喘不上气|呼吸困难|"
        r"(?:脸|嘴唇|眼皮).{0,4}肿|皮疹.{0,8}(?:增多|变多|连成片)|晕厥|"
        r"(?:拉肚子|腹泻).{0,8}(?:严重|不止|停不住|很多次|[五六七八九十]\s*次|发热|高烧)|"
        r"(?:严重|持续|频繁).{0,4}(?:拉肚子|腹泻)"
    )
    if re.search(severe_pattern, query):
        if "se_severe" in units:
            return "se_severe"
        if "alert_gut" in units:
            return "alert_gut"
    if re.search(r"喷.*眼|眼.*喷", query) and "od_action" in units:
        return "od_action"
    if re.search(r"(?:误服|误吃|一次|双倍|加倍).*(?:两粒|两片|两袋|多吃|过量)|(?:吃了|服用).*(?:两粒|两片|两袋)", query):
        if "od_action" in units:
            return "od_action"
    if re.search(r"(?:一天|每天).*(?:几次|几回|几顿)|(?:多久|隔多长).*一次", query):
        if "how_often" in units:
            return "how_often"
    if re.search(r"什么时候(?:吃|喝|用)|饭前|饭后|空腹|睡前|早上.*(?:吃|喝|用)|晚上.*(?:吃|喝|用)", query):
        if "how_timing" in units:
            return "how_timing"
        if "how_when" in units:
            return "how_when"
    if "冲" in query and "how_mix" in units:
        return "how_mix"
    if re.search(r"(?:怎么吃了|吃了|用了|服用后).*(?:没好|不好|没效果|不管用)", query) and "reject_no_effect" in units:
        return "reject_no_effect"
    if "喷" in query and "how_take" in units:
        return "how_take"
    # 口服补液盐的“腹泻时怎么喝”同时包含“喝”和按需使用时机，
    # 不能让笼统的“喝”规则盖过该药卡的 how_when 专属问法。
    if (
        "喝" in query
        and "how_take" in units
        and "how_when" not in units
        and not re.search(r"酒|啤酒|白酒|红酒|牛奶|咖啡|茶", query)
    ):
        return "how_take"
    if re.search(r"怎么(?:吃|服用|用)|如何(?:服用|使用)|怎样(?:服用|使用)", query):
        if "how_take" in units:
            return "how_take"
        if "how_mix" in units:
            return "how_mix"
    return None


def detect_intent_hints(question, card):
    """Return explicit intent hints from the user's wording in stable priority order."""
    query = clean_query(question, card).replace(" ", "")
    matches = []
    seen = set()
    for unit_id, pattern in INTENT_HINT_PATTERNS:
        if unit_id in seen or unit_id not in card["units"]:
            continue
        if re.search(pattern, query):
            matches.append(unit_id)
            seen.add(unit_id)
    return matches


def select_requested_units(question, card, top3, forced_unit_id=None):
    """Select up to three requested facts without letting the LLM choose facts."""
    hinted = detect_intent_hints(question, card)
    selected = []

    def add(unit_id):
        if unit_id and unit_id in card["units"] and unit_id not in selected:
            selected.append(unit_id)

    add(forced_unit_id)

    # A generic interaction phrase can outrank a more useful card-specific
    # conflict unit. Prefer a high-confidence specific conflict when the
    # wording clearly asks about co-administration.
    specific_conflict_id = None
    if "conflict_main" in hinted:
        specific_conflicts = [
            (unit_id, score)
            for unit_id, score in top3
            if unit_id.startswith("conflict_")
            and unit_id not in {"conflict_main", "conflict_note"}
            and score >= 0.80
        ]
        if specific_conflicts:
            specific_conflict_id = max(specific_conflicts, key=lambda item: item[1])[0]
            add(specific_conflict_id)

    for unit_id in hinted:
        if unit_id == "conflict_main" and specific_conflict_id:
            continue
        if unit_id == "not_cold" and "tcm_type" in hinted:
            continue
        if unit_id == "storage_how" and "storage_cold" in hinted:
            continue
        add(unit_id)

    # For a question without explicit intent words, preserve the original
    # semantic top-1 behavior. Explicit wording is what authorizes multi-fact
    # retrieval; otherwise a vague question should not collect unrelated facts.
    if not selected and top3:
        add(top3[0][0])
    reject_ids = [unit_id for unit_id in selected if unit_id.startswith("reject_")]
    if reject_ids:
        return reject_ids[:1], hinted
    return selected[:3], hinted


def selected_risk_level(selected_unit_ids, card):
    levels = [
        risk_level_for(unit_id, card["units"][unit_id]["answer"])
        for unit_id in selected_unit_ids
        if unit_id in card["units"]
    ]
    if "urgent" in levels:
        return "urgent"
    if "caution" in levels:
        return "caution"
    if "refusal" in levels:
        return "refusal"
    return "normal"


def retrieve(question, card, emb, variant_unit_ids, variant_vecs):
    query = str(clean_query(question, card))
    qv = emb.encode(
        [query],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype(np.float32, copy=False)
    sim = qv[0] @ variant_vecs.T
    # 问题银行由所有药共用；先过滤掉当前药没有的意图，再做 top-k。
    # 否则“布洛芬和感冒灵能同服吗”可能会被其他药才有的意图命中后拒答。
    valid_unit_ids = set(card["units"])
    best_by_unit = {}
    for i in range(len(variant_unit_ids)):
        scoped_id = variant_unit_ids[i]
        if CARD_VARIANT_SCOPE_SEPARATOR in scoped_id:
            scope, unit_id = scoped_id.split(CARD_VARIANT_SCOPE_SEPARATOR, 1)
            if scope != card["drug_name"]:
                continue
        else:
            unit_id = scoped_id
        if unit_id not in valid_unit_ids:
            continue
        score = float(sim[i])
        if score > best_by_unit.get(unit_id, float("-inf")):
            best_by_unit[unit_id] = score
    scores = sorted(best_by_unit.items(), key=lambda item: item[1], reverse=True)
    return query, scores[:5]


def is_ambiguous_match(top3):
    if len(top3) < 2:
        return False
    top_score = top3[0][1]
    margin = top3[0][1] - top3[1][1]
    return top_score < AMBIGUOUS_SCORE and margin < AMBIGUOUS_MARGIN


def risk_level_for(unit_id, answer):
    if unit_id in RISK_URGENT_UNITS or "立刻去医院" in answer or "马上停药" in answer:
        return "urgent"
    if unit_id.startswith(RISK_CAUTION_PREFIXES) or "不能" in answer or "不要" in answer or "不建议" in answer:
        return "caution"
    return "normal"


def call_http_json(url, payload, timeout=35):
    hostname = (urlparse(url).hostname or "").lower()
    loopback = hostname in {"127.0.0.1", "localhost", "::1"}
    if requests is not None:
        if loopback:
            session = requests.Session()
            session.trust_env = False
            resp = session.post(url, json=payload, timeout=timeout)
        else:
            resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if loopback
        else urllib.request.build_opener()
    )
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sanitize_llm_output(text):
    """Remove model reasoning/protocol leakage before validation or display."""
    cleaned = str(text or "")
    cleaned = re.sub(r"<think(?:\s[^>]*)?>.*?(?:</think>|$)", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<\|[^|>]+\|>", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*(?:problem_description|status|药名|问题|事实)\s*[:：].*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def is_incomplete_output(text):
    """A capped answer without sentence-ending punctuation is not safe to show."""
    normalized = sanitize_llm_output(text)
    if not normalized:
        return True
    return re.search(r"[。！？!?][”’）)】\]]*$", normalized) is None


def plan_answer_parts(approved_answers, target_chars=ANSWER_PART_TARGET_CHARS):
    """Group complete approved sentences into independently displayable parts."""
    parts = []
    pending = ""
    for approved_answer in dict.fromkeys(str(item).strip() for item in approved_answers if str(item).strip()):
        sentences = re.findall(r"[^。！？!?]+[。！？!?]?", approved_answer)
        if not sentences:
            sentences = [approved_answer]
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if pending and len(pending) + len(sentence) > target_chars:
                parts.append(pending)
                pending = ""
            # Do not split an individual approved sentence at a comma: doing so
            # can separate its condition from the medical action it qualifies.
            if not pending and len(sentence) > target_chars:
                parts.append(sentence)
            else:
                pending += sentence
    if pending:
        parts.append(pending)
    return parts or [""]


def _ollama_duration_ms(value):
    if not isinstance(value, (int, float)):
        return None
    # Ollama duration fields are nanoseconds; llama.cpp/OpenAI paths use their
    # own counters and are handled separately below.
    return round(value / 1_000_000, 1)


def call_llm_response(system_prompt, user_prompt, model, backend=None, url=None):
    """Return raw model text plus normalized token/duration metadata."""
    backend = backend or DEFAULT_LLM_BACKEND
    url = url or default_llm_url(backend)
    if "qwen3" in model.lower() and "/no_think" not in system_prompt:
        system_prompt = system_prompt + "\n/no_think"
    raw_prompt = RAW_CHATML_PROMPT.format(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    if backend == "ollama":
        payload = {
            "model": model,
            "prompt": raw_prompt,
            "think": False,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": LLM_OPTIONS,
        }
        data = call_http_json(url, payload, timeout=35)
        return data.get("response", "").strip(), {
            "backend": backend,
            "prompt_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
            "done_reason": data.get("done_reason"),
            "total_ms": _ollama_duration_ms(data.get("total_duration")),
            "load_ms": _ollama_duration_ms(data.get("load_duration")),
            "prompt_eval_ms": _ollama_duration_ms(data.get("prompt_eval_duration")),
            "eval_ms": _ollama_duration_ms(data.get("eval_duration")),
        }

    if backend == "llama_cpp_completion":
        payload = {
            "prompt": raw_prompt,
            "stream": False,
            "temperature": LLM_OPTIONS["temperature"],
            "top_p": LLM_OPTIONS["top_p"],
            "top_k": LLM_OPTIONS["top_k"],
            "min_p": LLM_OPTIONS["min_p"],
            "n_predict": LLM_OPTIONS["num_predict"],
            "repeat_penalty": LLM_OPTIONS["repeat_penalty"],
            "repeat_last_n": LLM_OPTIONS["repeat_last_n"],
            "cache_prompt": True,
        }
        data = call_http_json(url, payload, timeout=35)
        timings = data.get("timings") or {}
        return (data.get("content") or data.get("response") or "").strip(), {
            "backend": backend,
            "prompt_tokens": timings.get("prompt_n") or data.get("prompt_tokens"),
            "output_tokens": timings.get("predicted_n") or data.get("tokens_predicted"),
            "done_reason": data.get("stop_type") or data.get("done_reason"),
        }

    if backend == "llama_cpp_chat":
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "temperature": LLM_OPTIONS["temperature"],
            "top_p": LLM_OPTIONS["top_p"],
            "top_k": LLM_OPTIONS["top_k"],
            "min_p": LLM_OPTIONS["min_p"],
            "max_tokens": LLM_OPTIONS["num_predict"],
            "repeat_penalty": LLM_OPTIONS["repeat_penalty"],
            "repeat_last_n": LLM_OPTIONS["repeat_last_n"],
            "seed": 42,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            data = call_http_json(url, payload, timeout=35)
        except Exception as exc:
            # Only a concrete 4xx unknown-field response is a compatibility
            # signal.  A timeout/connection/5xx must never start a second full
            # generation after the caller already spent its deadline.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is None:
                status = getattr(exc, "code", None)
            detail = str(exc).lower()
            compatible = status in {400, 404, 422} and (
                "chat_template_kwargs" in detail or "unknown" in detail or "unexpected" in detail
            )
            if not compatible:
                raise
            payload.pop("chat_template_kwargs", None)
            data = call_http_json(url, payload, timeout=35)
        choices = data.get("choices") or []
        usage = data.get("usage") or {}
        if choices:
            message = choices[0].get("message") or {}
            finish_reason = choices[0].get("finish_reason")
            return (message.get("content") or choices[0].get("text") or "").strip(), {
                "backend": backend,
                "prompt_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "done_reason": finish_reason,
            }
        return "", {
            "backend": backend,
            "prompt_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "done_reason": None,
        }

    raise ValueError(f"未知 LLM 后端：{backend}")


def call_llm(system_prompt, user_prompt, model, backend=None, url=None):
    """Compatibility wrapper used by existing warm-up and smoke-test callers."""
    raw_text, _ = call_llm_response(system_prompt, user_prompt, model, backend=backend, url=url)
    return sanitize_llm_output(raw_text)


def required_terms(answer):
    groups = []
    if "马上停药" in answer:
        groups.append(("马上停药", "立即停药", "立刻停药"))
    if "立刻去医院" in answer:
        groups.append(("立刻去医院", "马上去医院", "立即去医院", "尽快去医院", "去急诊"))
    if "绝对不能用" in answer:
        groups.append(("绝对不能用", "不能用", "不要用", "不能吃", "不要吃"))
    if "不要同服" in answer:
        groups.append(("不要同服", "不能同服", "不要一起吃", "不能一起吃"))
    if "不建议" in answer:
        groups.append(("不建议", "不要自己", "不推荐"))
    if "不能自己" in answer:
        groups.append(("不能自己", "不要自己", "由医生", "让医生", "需由医生", "医生判断"))
    if "必须" in answer:
        groups.append(("必须", "一定要", "需要", "应当", "要按", "应按", "需按"))
    if "停药" in answer or "停用" in answer:
        groups.append(("停药", "停用"))
    if any(term in answer for term in ("就医", "急救", "急诊", "前往医院")):
        groups.append(("就医", "急救", "急诊", "医院", "医生"))
    if "禁用" in answer:
        groups.append(("禁用", "不能用", "不要用"))
    if "错开" in answer:
        groups.append(("错开", "间隔服用", "间隔开"))
    if "不是所有" in answer:
        groups.append(("不是所有", "并非所有", "不一定都", "并不是每种"))
    if "辨证" in answer:
        groups.append(("辨证", "辨证论治"))
    if "症状不符合" in answer:
        groups.append(("症状不符合", "症状不适合", "症状不对"))
    if "两三天没改善" in answer:
        groups.append(("两三天", "2到3天", "2-3天"))
        groups.append(("没改善", "没有改善", "不改善"))
    if "发热加重" in answer:
        groups.append(("发热加重", "发烧加重", "发热更重"))
    if "医生或药师" in answer:
        groups.append(("医生或药师", "医生、药师", "医生", "药师"))
    if "若" in answer or "如果" in answer:
        groups.append(("若", "如果", "出现", "当"))
    if any(term in answer for term in ("不要", "不能", "不应", "不宜", "禁止", "避免")):
        groups.append(("不要", "不能", "不应", "不宜", "禁止", "避免", "不可"))
    # 保持顺序去重，避免同一安全条件重复检查。
    unique = []
    for group in groups:
        if group not in unique:
            unique.append(group)
    return unique


def has_any(text, terms):
    return any(term in text for term in terms)


def has_positive_eligibility_claim(text):
    normalized = str(text or "").replace(" ", "")
    return bool(re.search(
        r"(?<!不)(?<!未)(?:可以|能(?:吃|服|用|继续)|适合(?:吃|服|用|继续)?|"
        r"建议(?:继续)?(?:吃|服|用))",
        normalized,
    ))


def extract_numbers(text):
    """提取数字表达，防止小模型凭空增加剂量、频次或疗程。"""
    text = text or ""
    arabic = set(re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?", text))
    chinese = set(
        re.findall(
            r"[零一二两三四五六七八九十百]+(?:毫克|克|毫升|片|粒|袋|次|天|小时|岁)",
            text,
        )
    )
    return arabic | chinese


def prompt_must_keep(answer):
    """把最容易被小模型弱化的数据库约束显式放进Prompt。"""
    anchors = (
        "绝对不能用",
        "不要催吐",
        "马上停药",
        "立刻去医院",
        "不能自己",
        "不要和",
        "不要同服",
        "不建议",
        "必须",
        "谨慎",
        "急诊",
        "不要开车",
        "跳过这次不补",
    )
    keep = sorted(extract_numbers(answer))
    keep.extend(term for term in anchors if term in answer and term not in keep)
    return "；".join(keep) if keep else "批准答案中的全部核心事实"


def validate_llm_answer(llm_answer, standard_answer, question, unit_id, risk_level, must_keep=()):
    if not llm_answer:
        return False, "空输出"
    if INSTRUCTIONAL_PREFIX_PATTERN.search(llm_answer):
        return False, "指令性前缀"
    for marker in PROTOCOL_MARKERS:
        if marker in llm_answer:
            return False, f"协议文本：{marker}"
    if llm_answer.replace("；", "").replace(" ", "") == prompt_must_keep(standard_answer).replace("；", ""):
        return False, "只输出了最低保留词"
    if llm_answer.strip("。？！? ") == question.strip("。？！? "):
        return False, "复读问题"
    first_sentence = re.split(r"[。！？!?]", llm_answer, maxsplit=1)[0].strip()
    if re.match(r"^(?:您|你)(?:是|就是|为)", first_sentence):
        return False, "把用户和药品混为一体"
    if SELF_MEDICATION_PATTERN.search(llm_answer) and not SELF_MEDICATION_PATTERN.search(standard_answer):
        return False, "无来源地肯定可以自行用药"
    bad_refusals = ("暂无相关", "没有相关", "说明书上没有写", "数据库里没有")
    if not unit_id.startswith("reject_") and any(item in llm_answer for item in bad_refusals):
        return False, "错误拒答"
    uncertain_refusals = ("不能确定", "无法确定", "不确定", "无法判断")
    source_is_uncertain = any(term in standard_answer for term in uncertain_refusals)
    if not source_is_uncertain and any(term in llm_answer for term in uncertain_refusals):
        return False, "批准答案明确但模型拒答"
    # Approved indications must not be reversed into "cannot/not recommended".
    # This is deliberately narrow so ordinary safety constraints keep working.
    if unit_id == "what_treat" and any(term in standard_answer for term in ("可用于", "可以用于", "适用于")):
        negative_indication = ("不能治疗", "不建议用于", "不适用于", "不推荐用于", "不能用于")
        if any(term in llm_answer for term in negative_indication) and not any(
            term in standard_answer for term in negative_indication
        ):
            return False, "把已批准适用范围改成否定"
    if "请按本盒说明书" in standard_answer and not any(
        term in llm_answer for term in ("按本盒说明书", "按药盒说明书", "说明书或医生、药师")
    ):
        return False, "遗漏按药盒说明书使用条件"
    if len(llm_answer) > max(170, len(standard_answer) * 2 + 30):
        return False, "输出过长"
    # 防止小模型把 QUESTION / must_preserve 中的零散词直接吐在答案开头，
    # 例如“`一天；必须。`”，这既不自然，也不属于批准答案的完整复述。
    if any(marker in first_sentence for marker in ("？", "?", "吗")):
        return False, "复述或改写了用户问题"
    if "；" in first_sentence and len(first_sentence) <= 14:
        fragments = [part.strip() for part in first_sentence.split("；") if part.strip()]
        allowed_fragments = set(prompt_must_keep(standard_answer).split("；"))
        if fragments and all(part in allowed_fragments or part in question for part in fragments):
            return False, "输出了提示词碎片"

    # 封闭域不能允许模型把原文没有的风险、疗效或因果关系加进去。
    # 这组短语覆盖0.6B最常见的“为了说得完整而补充”的幻觉。
    invented_markers = (
        "不良反应", "影响药物效果", "影响药效", "降低药效", "治疗效果",
        "危及生命", "产生副作用", "导致肾损伤", "导致肝损伤",
    )
    for marker in invented_markers:
        if marker in llm_answer and marker not in standard_answer:
            return False, f"新增无来源风险或疗效：{marker}"
    if unit_id == "what_treat":
        # “主要用于/常用于”不能被小模型改写成未经批准的否定适应范围。
        source_positive = any(term in standard_answer for term in (
            "可用于", "可以用于", "适用于", "主要用于", "常用于",
        ))
        source_negative = any(term in standard_answer for term in (
            "不能治疗", "不适用于", "不建议用于", "不能用于",
        ))
        output_negative = any(term in llm_answer for term in (
            "不能治疗", "不适用于", "不建议用于", "不能用于",
        ))
        if source_positive and not source_negative and output_negative:
            return False, "把已批准适用范围改成否定"
        causal_terms = ("引起", "导致", "造成")
        if any(term in llm_answer for term in causal_terms) and not any(
            term in standard_answer for term in causal_terms
        ):
            return False, "新增无来源因果关系"
        # “辅助补充/预防”被改成“治疗”会扩大医疗含义，宁可回退卡片原文。
        if "治疗" in llm_answer and "治疗" not in standard_answer and any(
            term in standard_answer for term in ("辅助补充", "预防", "适用范围需")
        ):
            return False, "把辅助补充或预防扩大成治疗"
    # 这些是药品FAQ里不能被“简化掉”的事实槽；允许常见同义表达。
    fact_groups = (
        (("乏力",), ("乏力", "疲乏", "疲倦")),
        (("头晕",), ("头晕", "眩晕")),
        (("头孢过敏",), ("头孢过敏", "对头孢过敏")),
        (("严重皮疹",), ("严重皮疹", "重度皮疹")),
        (("肝损伤",), ("肝损伤", "肝脏损伤")),
        (("量多",), ("量多", "吃得很多", "服用过多")),
        (("不舒服明显",), ("不舒服明显", "明显不舒服", "症状明显")),
        (("低烧",), ("低烧", "低热")),
        (("观察",), ("观察", "先看情况")),
        (("补水",), ("补水", "补充水分")),
        (("重复抗过敏",), ("重复抗过敏", "抗过敏成分重复")),
        (("更困",), ("更困", "更加嗜睡", "困倦加重")),
    )
    for source_terms, output_terms in fact_groups:
        if any(term in standard_answer for term in source_terms) and not any(term in llm_answer for term in output_terms):
            return False, f"遗漏事实：{'/'.join(source_terms)}"
    allowed_numbers = extract_numbers(standard_answer) | extract_numbers(question)
    extra_numbers = extract_numbers(llm_answer) - allowed_numbers
    if extra_numbers:
        return False, f"新增数字：{','.join(sorted(extra_numbers))}"
    if "可以" in llm_answer:
        source_prohibits = any(
            term in standard_answer
            for term in ("绝对不能用", "不能用", "不要同服", "不要和", "不建议", "禁止", "避免")
        )
        if source_prohibits:
            return False, "把数据库限制改成可以"
    if is_eligibility_query(question) and has_positive_eligibility_claim(llm_answer):
        if not has_positive_eligibility_claim(standard_answer):
            return False, "无来源地肯定特殊身体状态下可用药"
    source_is_cautious = any(term in standard_answer for term in ("随便同服", "谨慎", "不建议"))
    source_is_absolute = any(term in standard_answer for term in ("绝对不能", "禁止", "严禁"))
    output_is_absolute = any(term in llm_answer for term in (
        "绝对不能", "不能一起吃", "不能同服", "不能同时服用", "禁止同服",
    ))
    if source_is_cautious and not source_is_absolute and output_is_absolute:
        return False, "把谨慎限制强化成绝对禁止"
    if unit_id == "se_causality":
        uncertainty = ("不能", "无法", "不好确定", "不能确定", "不能只凭", "没法只凭")
        if not any(term in llm_answer for term in uncertainty):
            return False, "没有保留因果不确定"
        dangerous = (
            "可能是",
            "应该是",
            "就是",
            "大概率是",
            "可能与",
            "可能和",
            "可能跟",
        )
        causal = ("导致", "引起", "造成", "有关", "相关")
        if any(a in llm_answer for a in dangerous) and any(b in llm_answer for b in causal):
            return False, "擅自判断因果"
    if "服后若" in standard_answer:
        # “困倦/反应变慢时不要驾驶”是条件限制，不能被改成所有服药者都不能驾驶。
        conditional_pattern = (
            r"(?:若|如果|当).{0,16}(?:困倦|反应变慢|嗜睡|疲倦)"
            r"|(?:困倦|反应变慢|嗜睡|疲倦).{0,8}(?:时|后)"
        )
        if not re.search(conditional_pattern, llm_answer):
            return False, "遗漏驾驶限制的触发条件"
    for term in must_keep:
        if term and term not in llm_answer:
            return False, f"遗漏卡片锁定事实：{term}"
    for terms in required_terms(standard_answer):
        if not has_any(llm_answer, terms):
            label = "丢安全词" if risk_level == "urgent" else "丢约束词"
            return False, f"{label}：{'/'.join(terms)}"
    return True, "OK"


def rewrite_answer(
    question,
    card,
    unit_id,
    bank_unit,
    card_unit,
    standard_answer,
    model,
    llm_backend=None,
    llm_url=None,
    risk_level_override=None,
):
    risk_level = risk_level_override or risk_level_for(unit_id, standard_answer)
    user_prompt = USER_PROMPT.format(
        question=question,
        drug_name=card["drug_name"],
        unit_id=unit_id,
        category=bank_unit["category"],
        sub=bank_unit["sub"],
        risk_level=risk_level,
        answer=standard_answer,
        must_keep=prompt_must_keep(standard_answer),
    )
    llm_raw, llm_meta = call_llm_response(
        SYSTEM_PROMPT,
        user_prompt,
        model,
        backend=llm_backend,
        url=llm_url,
    )
    llm_meta = {**llm_meta, "prompt_version": DEFAULT_PROMPT_VERSION}
    llm_answer = sanitize_llm_output(llm_raw)
    output_tokens = llm_meta.get("output_tokens")
    done_reason = str(llm_meta.get("done_reason") or "").lower()
    if done_reason == "length" or (
        isinstance(output_tokens, (int, float)) and output_tokens >= LLM_OPTIONS["num_predict"]
    ) or is_incomplete_output(llm_answer):
        return standard_answer, "模型输出达到长度上限或不完整", risk_level, llm_raw, llm_meta
    ok, reason = validate_llm_answer(
        llm_answer, standard_answer, question, unit_id, risk_level,
        card_unit.get("must_keep", ()),
    )
    if not ok:
        return standard_answer, reason, risk_level, llm_raw, llm_meta
    return llm_answer, "OK", risk_level, llm_raw, llm_meta


def answer_once(
    question,
    cards,
    current_drug,
    bank,
    emb,
    variant_unit_ids,
    variant_vecs,
    use_llm,
    model,
    llm_backend=None,
    llm_url=None,
):
    urgent_signal = global_urgent_signal(question)
    if urgent_signal:
        # Safety triage runs before drug matching so an unknown or legacy drug
        # cannot hide an active emergency symptom behind an unlisted-drug reply.
        # Never attach a previous medicine to an emergency that explicitly
        # names another, unrecognized medicine. The urgent advice is generic;
        # a stale drug_name in the API response would mislead the frontend.
        explicit_unlisted = (
            has_explicit_legacy_drug(question, cards)
            or looks_like_unlisted_drug(question, cards)
            or looks_like_unknown_drug_reference(question)
        )
        card, matched_name = (None, None) if explicit_unlisted else find_drug(question, cards, None)
        if card is None and not explicit_unlisted:
            latin_card, latin_alias = find_latin_alias(question, cards)
            if latin_card is not None:
                card, matched_name = latin_card, latin_alias
        next_drug = card if card is not None else (None if explicit_unlisted else current_drug)
        return {
            "card": card,
            "matched_name": matched_name,
            "answer": global_urgent_answer(urgent_signal),
            "answer_parts": [global_urgent_answer(urgent_signal)],
            "new_current_drug": next_drug,
            "top3": [],
            "top_unit_id": "global_urgent",
            "top_score": 1.0,
            "selected_unit_ids": [],
            "hinted_unit_ids": [],
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "urgent",
            "fallback_reason": "global_urgent_fixed_answer",
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "done_reason": "not_called"},
        }
    # Retired full names need priority over active substring aliases (for
    # example 左西替利嗪 versus 西替利嗪). Other unlisted checks run after
    # pinyin correction so “仁森健脾片” can still resolve safely.
    if has_explicit_legacy_drug(question, cards):
        return unlisted_drug_result()
    if has_ambiguous_compound_cold_name(question) and re.search(r"能和|还能|一起|同服|合用", question):
        answer = "药名不完整，不能据此强行判断两种药能否同服。请核对药盒上的完整中文药名后再问。"
        return {
            "card": None,
            "matched_name": None,
            "answer": answer,
            "answer_parts": [answer],
            "new_current_drug": None,
            "top3": [],
            "top_unit_id": None,
            "top_score": 0.0,
            "selected_unit_ids": [],
            "hinted_unit_ids": [],
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "normal",
            "fallback_reason": "ambiguous_drug_reference",
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "done_reason": "not_called"},
        }

    # A dosage form plus a quality-damage signal is enough for a conservative
    # safety response, but it must not inherit an unrelated medicine from the
    # previous turn.  Named medicines continue into their card-specific rule.
    explicit_card, explicit_matched_name = find_drug(question, cards, None)
    latin_alias_card, latin_alias = find_latin_alias(question, cards)
    if explicit_card is None and latin_alias_card is not None:
        explicit_card = latin_alias_card
        explicit_matched_name = latin_alias
        canonical_name = latin_alias_card["drug_name"]
        question = re.sub(
            r"(?<![A-Za-z])" + re.escape(latin_alias) + r"(?![A-Za-z])",
            canonical_name,
            question,
            count=1,
            flags=re.IGNORECASE,
        )
    if storage_damage_signal(question) and explicit_card is None:
        answer = generic_storage_damage_answer()
        return {
            "card": None,
            "matched_name": None,
            "answer": answer,
            "answer_parts": [answer],
            "new_current_drug": None,
            "top3": [],
            "top_unit_id": "storage_damage",
            "top_score": 1.0,
            "selected_unit_ids": ["storage_damage"],
            "hinted_unit_ids": [],
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "caution",
            "fallback_reason": "global_storage_damage_fixed_answer",
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "done_reason": "not_called"},
        }

    if explicit_card is None:
        fuzzy_card, fuzzy_name, fuzzy_detail = fuzzy_candidate(question, cards)
        if fuzzy_card is not None and fuzzy_detail and fuzzy_detail.get("autoAccepted"):
            # High-confidence pinyin/typo matches are corrected silently. Every
            # downstream route receives the card's canonical drug_name.
            explicit_card = fuzzy_card
            explicit_matched_name = fuzzy_name
            question = canonicalize_drug_question(
                question, fuzzy_detail, fuzzy_card["drug_name"]
            )
    if explicit_card is None and looks_like_unlisted_drug(question, cards):
        return unlisted_drug_result()
    # Do this before allowing find_drug() to fall back to the prior turn.
    # A typo, English name, or another unrecognized drug must never inherit a
    # different medicine's usage instructions.
    if explicit_card is None and looks_like_unknown_drug_reference(question):
        return {
            "card": None,
            "matched_name": None,
            "answer": "药名未能准确识别，不能沿用上一种药的答案。请核对药盒上的中文通用名后再问。",
            "answer_parts": [],
            "new_current_drug": None,
            "top3": [],
            "top_unit_id": None,
            "top_score": 0.0,
            "selected_unit_ids": [],
            "hinted_unit_ids": [],
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "normal",
            "fallback_reason": "unknown_drug_reference",
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "done_reason": "not_called"},
        }

    if explicit_card is not None:
        card, matched_name = explicit_card, explicit_matched_name
    else:
        card, matched_name = find_drug(question, cards, current_drug)
    if card is None:
        return {
            "card": None,
            "answer": "请先说清楚是哪一种药，比如阿莫西林、布洛芬、蒙脱石散或西替利嗪。",
            "new_current_drug": None if looks_like_unknown_drug_reference(question) else current_drug,
        }

    # 纯药名不是“知识问答题”，而是一次上下文切换。固定回复避免无意义
    # 检索和 LLM 调用；后续带“它/这个药”的问题会沿用 new_current_drug。
    if is_drug_name_only(question, card, matched_name):
        return {
            "card": card,
            "matched_name": matched_name,
            "answer": drug_context_greeting(card),
            "new_current_drug": card,
            "context_only": True,
            "top3": [],
            "top_unit_id": "drug_context_selected",
            "top_score": 1.0,
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "normal",
            "fallback_reason": "drug_context_selected",
        }

    if storage_damage_signal(question) and "storage_damage" not in card["units"]:
        answer = generic_storage_damage_answer()
        return {
            "card": card,
            "matched_name": matched_name,
            "answer": answer,
            "answer_parts": [answer],
            "new_current_drug": card,
            "top3": [],
            "top_unit_id": "storage_damage",
            "top_score": 1.0,
            "selected_unit_ids": ["storage_damage"],
            "hinted_unit_ids": [],
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "caution",
            "fallback_reason": "global_storage_damage_fixed_answer",
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "done_reason": "not_called"},
        }

    if is_suitable_population_query(question, card):
        answer_parts, answer = suitable_population_answer(card)
        return {
            "card": card,
            "matched_name": matched_name,
            "answer": answer,
            "answer_parts": answer_parts,
            "new_current_drug": card,
            "top3": [],
            "top_unit_id": "suitable_population",
            "top_score": 1.0,
            "selected_unit_ids": ["suitable_population"],
            "hinted_unit_ids": [],
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "caution",
            "fallback_reason": "suitable_population_fixed_answer",
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "done_reason": "not_called"},
        }

    if is_unspecified_quality_query(question, card):
        answer = "我不能只根据“好不好用”判断药效。请说清楚您想改善的具体症状或用药后的变化。"
        return {
            "card": card,
            "matched_name": matched_name,
            "answer": answer,
            "answer_parts": [answer],
            "new_current_drug": card,
            "top3": [],
            "top_unit_id": None,
            "top_score": 0.0,
            "selected_unit_ids": [],
            "hinted_unit_ids": [],
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "normal",
            "fallback_reason": "vague_quality_fixed_answer",
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "done_reason": "not_called"},
        }

    condition_resolution = resolve_condition_eligibility(question, card)
    if condition_resolution and condition_resolution["unit_id"] is None:
        answer = uncovered_condition_answer(card)
        return {
            "card": card,
            "matched_name": matched_name,
            "answer": answer,
            "answer_parts": [answer],
            "new_current_drug": card,
            "top3": [],
            "top_unit_id": None,
            "top_score": 0.0,
            "selected_unit_ids": [],
            "embed_ms": 0.0,
            "llm_ms": 0.0,
            "risk_level": "caution",
            "fallback_reason": "uncovered_condition_fixed_answer",
            "token_usage": {
                "prompt_tokens": 0,
                "output_tokens": 0,
                "done_reason": "not_called",
            },
        }

    # Keep explicit operation, interaction, special-population and emergency
    # routes ahead of treatment facts.  They are still reviewed card facts,
    # rather than an LLM-made medical judgment.
    intent_forced_unit_id = forced_intent(question, card)
    conflict_question = is_medication_conflict_question(question, cards)
    indication_resolution = resolve_approved_indication(question, card, cards)
    if not intent_forced_unit_id and not indication_resolution:
        # These facts explicitly answer a suitability question and are safer
        # than a generic "not covered" response. They are still card-scoped.
        eligibility_hints = detect_intent_hints(question, card)
        intent_forced_unit_id = next(
            (
                unit_id
                for unit_id in eligibility_hints
                if unit_id in {"not_cold", "tcm_type", "reject_unlisted_disease"}
            ),
            None,
        )
    # A treatment question is always answered from the medicine card when the
    # card has a reviewed what_treat unit.  The approved-term index can provide
    # a more precise route, but an empty index must not hide the card fact.
    # This is deliberately fact-first: the card's wording remains unchanged,
    # and no new indication is inferred from the user's symptom.
    if (
        is_treatment_eligibility_query(question, card)
        and not conflict_question
        and not (condition_resolution and condition_resolution["unit_id"])
        and not intent_forced_unit_id
        and not indication_resolution
    ):
        if has_treatment_fact(card):
            indication_resolution = {"unit_id": "what_treat", "card_fact": True}
        else:
            answer = uncovered_indication_answer(card)
            return {
                "card": card,
                "matched_name": matched_name,
                "answer": answer,
                "answer_parts": [answer],
                "new_current_drug": card,
                "top3": [],
                "top_unit_id": None,
                "top_score": 0.0,
                "selected_unit_ids": [],
                "hinted_unit_ids": [],
                "embed_ms": 0.0,
                "llm_ms": 0.0,
                "risk_level": "caution",
                "fallback_reason": "uncovered_indication_fixed_answer",
                "token_usage": {
                    "prompt_tokens": 0,
                    "output_tokens": 0,
                    "done_reason": "not_called",
                },
            }
    t0 = time.time()
    query, top3 = retrieve(question, card, emb, variant_unit_ids, variant_vecs)
    forced_unit_id = (
        condition_resolution["unit_id"]
        if condition_resolution and condition_resolution["unit_id"]
        else intent_forced_unit_id or (
            indication_resolution["unit_id"] if indication_resolution else None
        )
    )
    if forced_unit_id:
        top3 = [(forced_unit_id, 1.0)] + [item for item in top3 if item[0] != forced_unit_id]
    top_unit_id, top_score = top3[0] if top3 else (None, 0.0)
    selected_unit_ids, hinted_unit_ids = select_requested_units(
        question, card, top3, forced_unit_id
    )
    # Keep the primary result aligned with the first explicitly requested fact
    # instead of allowing a nearby generic embedding match to take priority.
    if selected_unit_ids and (forced_unit_id or hinted_unit_ids):
        score_by_unit = dict(top3)
        preferred_unit_id = selected_unit_ids[0]
        preferred_score = score_by_unit.get(preferred_unit_id, top_score)
        top3 = [(preferred_unit_id, preferred_score)] + [
            item for item in top3 if item[0] != preferred_unit_id
        ]
        top_unit_id, top_score = top3[0]
    explicit_intent = bool(forced_unit_id or hinted_unit_ids)
    embed_ms = (time.time() - t0) * 1000

    result = {
        "card": card,
        "matched_name": matched_name,
        "query": query,
        "top3": top3,
        "top_unit_id": top_unit_id,
        "top_score": top_score,
        "selected_unit_ids": selected_unit_ids,
        "hinted_unit_ids": hinted_unit_ids,
        "embed_ms": embed_ms,
        "new_current_drug": card,
    }

    # Similarity alone is not enough for a drug-conflict answer.  A question
    # needs both a co-medication relationship and another medicine reference;
    # otherwise unrelated text can resemble a conflict variant by accident.
    if (
        any(unit_id in CONFLICT_ROUTE_UNITS for unit_id in selected_unit_ids)
        and not is_medication_conflict_question(question, cards)
    ):
        result["top_unit_id"] = None
        result["selected_unit_ids"] = []
        result["answer"] = f"抱歉，我没能把这个问题匹配到{card['drug_name']}的用法、安全或注意事项。"
        result["fallback_reason"] = "out_of_domain_low_match"
        return result

    if (
        not selected_unit_ids
        or top_unit_id not in card["units"]
        or (
            not explicit_intent
            and (top_score < MIN_MATCH_SCORE or is_ambiguous_match(top3))
        )
    ):
        result["answer"] = f"抱歉，我没能把这个问题匹配到{card['drug_name']}的用法、安全或注意事项。"
        result["fallback_reason"] = (
            "匹配不确定" if is_ambiguous_match(top3) else "无匹配或该药无答案"
        )
        return result

    unit = card["units"][top_unit_id]
    bank_unit = bank["units"][top_unit_id]
    score_by_unit = dict(top3)
    selected_facts = []
    standard_answers = []
    must_keep = []
    for unit_id in selected_unit_ids:
        card_unit = card["units"][unit_id]
        bank_item = bank["units"][unit_id]
        answer = card_unit["answer"]
        if answer not in standard_answers:
            standard_answers.append(answer)
        for term in card_unit.get("must_keep", ()):
            if term not in must_keep:
                must_keep.append(term)
        selected_facts.append({
            "factId": unit_id,
            "category": bank_item.get("category"),
            "factName": bank_item.get("sub"),
            "score": round(score_by_unit.get(unit_id, 1.0 if unit_id in hinted_unit_ids else 0.0), 4),
            "answer": answer,
        })
    standard_answer = "\n".join(standard_answers)
    result["unit"] = unit
    result["bank_unit"] = bank_unit
    result["selected_facts"] = selected_facts
    result["standard_answers"] = standard_answers
    result["standard_answer"] = standard_answer
    result["answer_parts"] = plan_answer_parts(standard_answers)
    result["token_usage"] = {
        "prompt_tokens": 0,
        "output_tokens": 0,
        "done_reason": "not_called",
    }
    planned_answer = "\n".join(result["answer_parts"])

    risk_level = selected_risk_level(selected_unit_ids, card)
    has_reject = any(unit_id.startswith("reject_") for unit_id in selected_unit_ids)
    has_condition_source = any(unit_id.startswith("ci_") for unit_id in selected_unit_ids)
    has_direct_source = has_condition_source or any(
        is_fact_first_source(unit_id, card["units"][unit_id]["answer"])
        for unit_id in selected_unit_ids
        if unit_id in card["units"]
    )
    # 越界拒答和紧急行动必须是确定性的：不能让0.6B借题发挥、弱化
    # “部分”、停药或就医条件。它们已是知识卡中审核过的自然语言答案。
    if has_reject:
        result["answer"] = planned_answer
        result["risk_level"] = "refusal"
        result["llm_ms"] = 0
        result["fallback_reason"] = "controlled_refusal"
        return result
    if risk_level == "urgent":
        result["answer"] = planned_answer
        result["risk_level"] = "urgent"
        result["llm_ms"] = 0
        result["fallback_reason"] = "urgent_fixed_answer"
        return result
    if has_direct_source:
        result["answer"] = planned_answer
        result["risk_level"] = risk_level
        result["llm_ms"] = 0
        result["fallback_reason"] = (
            "condition_fixed_answer" if has_condition_source else "controlled_instruction"
        )
        return result

    # A 0.6B model capped at 88 tokens cannot safely continue a truncated
    # answer.  When multiple approved facts/sentences are needed, return the
    # preplanned parts as separate display messages instead of serial calls.
    if len(result["answer_parts"]) > 1:
        result["answer"] = "\n".join(result["answer_parts"])
        result["risk_level"] = risk_level
        result["llm_ms"] = 0
        result["fallback_reason"] = "preplanned_answer_parts"
        return result

    if not use_llm:
        result["answer"] = planned_answer
        result["risk_level"] = risk_level
        result["llm_ms"] = 0
        result["fallback_reason"] = "raw"
        return result

    t1 = time.time()
    try:
        merged_unit = {"must_keep": tuple(must_keep)}
        answer, reason, risk_level, llm_raw, llm_meta = rewrite_answer(
            question,
            card,
            top_unit_id,
            bank_unit,
            merged_unit,
            standard_answer,
            model,
            llm_backend,
            llm_url,
            risk_level_override=risk_level,
        )
        result["answer"] = answer
        result["fallback_reason"] = reason
        result["risk_level"] = risk_level
        result["llm_raw"] = llm_raw
        result["token_usage"] = llm_meta
        result["answer_parts"] = [answer]
    except Exception as exc:
        result["answer"] = planned_answer
        result["fallback_reason"] = f"LLM失败：{exc}"
        result["risk_level"] = risk_level
    result["llm_ms"] = (time.time() - t1) * 1000
    return result


def run_self_test(bank, cards, emb, variant_unit_ids, variant_vecs):
    tests = [
        ("三金西瓜霜怎么喷", "三金西瓜霜", "how_take"),
        ("人参健脾片是治什么的", "人参健脾片", "what_treat"),
        ("口服补液盐III怎么冲", "口服补液盐散（III）", "how_mix"),
        ("复合维生素B尿黄正常吗", "复合维生素B片", "se_urine_yellow"),
        ("复方感冒灵里面有什么成分", "复方感冒灵颗粒", "ingredient_warn"),
        ("复方金银花是所有感冒都能吃吗", "复方金银花颗粒", "tcm_type"),
        ("孟鲁司特是急救药吗", "孟鲁司特钠片", "how_when"),
        ("小柴胡颗粒是治什么的", "小柴胡颗粒", "what_treat"),
        ("阿莫西林一般会产生什么副作用", "阿莫西林胶囊", "se_common"),
        ("复方氨酚烷胺里面有什么成分", "复方氨酚烷胺胶囊", "ingredient_warn"),
        ("氨氯地平血压正常了能停吗", "氨氯地平片", "stop_never"),
        ("双歧杆菌能和阿莫西林一起吃吗", "双歧杆菌三联活菌胶囊", "conflict_antibiotic"),
        ("蒙脱石散怎么冲", "蒙脱石散", "how_mix"),
        ("盐酸多西环素能和钙片一起吃吗", "盐酸多西环素片", "conflict_main"),
        ("西替利嗪能和复方氨酚烷胺一起吗", "盐酸西替利嗪片", "conflict_duplicate_antihistamine"),
        ("维生素D滴剂的功效是什么", "维生素D滴剂", "what_treat"),
        ("金银花口服液是治什么的", "金银花口服液", "what_treat"),
    ]
    passed = 0
    by_name = {card["drug_name"]: card for card in cards}
    for question, drug_name, expected in tests:
        card = by_name[drug_name]
        query, top3 = retrieve(question, card, emb, variant_unit_ids, variant_vecs)
        top_unit_id, top_score = top3[0]
        # 与正式 answer_once 一致：特定高风险问法优先走确定性路由。
        top_unit_id = forced_intent(question, card) or top_unit_id
        ok = top_unit_id == expected and top_unit_id in card["units"]
        passed += int(ok)
        print(f"{'OK' if ok else 'XX'} {question}")
        print(f"  药品: {drug_name} | 检索词: {query}")
        print(f"  匹配: {top_unit_id} score={top_score:.3f} expected={expected}")
    nonsense = answer_once(
        "小柴胡吃了能发财吗",
        cards,
        None,
        bank,
        emb,
        variant_unit_ids,
        variant_vecs,
        False,
        DEFAULT_MODEL,
    )
    ok = nonsense["fallback_reason"] in {"匹配不确定", "无匹配或该药无答案"}
    passed += int(ok)
    print(f"{'OK' if ok else 'XX'} 小柴胡吃了能发财吗")
    print(f"  过滤: {nonsense.get('fallback_reason')} | 答案: {nonsense['answer']}")
    quality_tests = [
        (
            "llm_rejects_user_drug_mixup",
            "您是阿莫西林，是一种青霉素类抗生素，用于治疗细菌感染。",
            "阿莫西林是一种青霉素类抗生素，用于治疗部分细菌感染。",
            "阿莫西林是什么药",
            "what_is",
            "normal",
            False,
        ),
        (
            "llm_keeps_tcm_dialectic_conditions",
            "您可根据自身情况咨询医生或药师，小柴胡颗粒需根据具体症状和体质使用。",
            "中成药讲究辨证。症状不符合、吃两三天没改善，或发热加重，要问医生或药师。",
            "小柴胡颗粒是不是所有人都能喝",
            "tcm_type",
            "caution",
            False,
        ),
        (
            "llm_rejects_instructional_prefix",
            "按“阿莫西林胶囊”回答：胶囊应整粒吞服。",
            "阿莫西林胶囊应整粒吞服。",
            "阿莫西林胶囊怎么吃",
            "how_take",
            "normal",
            False,
        ),
        (
            "llm_rejects_unsourced_self_medication",
            "您可以自行服用复方金银花颗粒。",
            "复方金银花颗粒不是所有感冒都适合。",
            "复方金银花颗粒是所有感冒都能吃吗",
            "not_cold",
            "caution",
            False,
        ),
        (
            "llm_allows_normal_drug_subject",
            "阿莫西林胶囊应整粒吞服。",
            "阿莫西林胶囊应整粒吞服。",
            "阿莫西林胶囊怎么吃",
            "how_take",
            "normal",
            True,
        ),
    ]
    for test_id, candidate, standard, question, unit_id, risk_level, expected_accept in quality_tests:
        accepted, reason = validate_llm_answer(
            candidate, standard, question, unit_id, risk_level
        )
        ok = accepted == expected_accept
        passed += int(ok)
        print(f"{'OK' if ok else 'XX'} 质检: {test_id}")
        print(f"  结果: {reason}")
    multi_tests = [
        ("西瓜霜能不能治疗口腔溃疡", "三金西瓜霜", {"what_treat"}),
        ("脾胃虚弱腹痛便溏可以用人参健脾片吗", "人参健脾片", {"what_treat"}),
        ("口服补液盐III腹泻时怎么喝", "口服补液盐散（III）", {"how_when"}),
        ("阿莫西林一般会产生什么副作用", "阿莫西林胶囊", {"se_common"}),
        ("阿莫西林的功效和副作用是什么", "阿莫西林胶囊", {"what_treat", "se_common"}),
        ("蒙脱石散一天几次、怎么冲", "蒙脱石散", {"how_often", "how_mix"}),
        ("西替利嗪能和复方氨酚烷胺一起吗", "盐酸西替利嗪片", {"conflict_duplicate_antihistamine"}),
        ("阿莫西林有什么禁忌和注意事项", "阿莫西林胶囊", {"ci_allergy", "how_note"}),
    ]
    for question, drug_name, expected_ids in multi_tests:
        result = answer_once(
            question,
            cards,
            None,
            bank,
            emb,
            variant_unit_ids,
            variant_vecs,
            False,
            DEFAULT_MODEL,
        )
        actual_ids = set(result.get("selected_unit_ids") or [])
        ok = actual_ids == expected_ids
        passed += int(ok)
        print(f"{'OK' if ok else 'XX'} {question}")
        print(f"  多事实: {sorted(actual_ids)} expected={sorted(expected_ids)}")
    regression_passed, regression_total = run_regression_self_test(
        bank, cards, emb, variant_unit_ids, variant_vecs
    )
    passed += regression_passed
    total = len(tests) + 1 + len(quality_tests) + len(multi_tests) + regression_total
    print(f"测试结果: {passed}/{total}")
    return passed == total


def run_regression_self_test(bank, cards, emb, variant_unit_ids, variant_vecs):
    """Run the maintained route/anchor regression cases without calling the LLM."""
    cases_path = BASE_DIR / "qa_regression_cases.json"
    if not cases_path.is_file():
        print("回归清单缺失，跳过")
        return 0, 0
    cases = load_json(cases_path).get("cases", [])
    by_name = {card["drug_name"]: card for card in cards}
    passed = 0
    for case in cases:
        question = case["question"]
        card = by_name.get(case.get("drug"))
        result = answer_once(
            question,
            cards,
            card,
            bank,
            emb,
            variant_unit_ids,
            variant_vecs,
            False,
            DEFAULT_MODEL,
        )
        actual_ids = set(result.get("selected_unit_ids") or [])
        actual_unit = result.get("top_unit_id")
        if actual_unit != "global_urgent" and case.get("expected_unit"):
            actual_unit = case["expected_unit"] if case["expected_unit"] in actual_ids else actual_unit
        if (
            actual_unit == "global_urgent"
            or result.get("risk_level") == "urgent"
            or result.get("fallback_reason") == "global_storage_damage_fixed_answer"
        ):
            actual_route = "fixed_answer"
        elif actual_ids and (
            any(item.startswith(("ci_", "reject_", "conflict_", "stop_")) for item in actual_ids)
            or any(item in DIRECT_SOURCE_UNITS for item in actual_ids)
        ):
            actual_route = "fixed_answer"
        elif actual_ids:
            actual_route = "send_to_llm_rewrite"
        else:
            actual_route = "fixed_answer"
        answer = str(result.get("answer") or "")
        actual_card_name = (result.get("card") or {}).get("drug_name")
        actual_next_drug_name = (result.get("new_current_drug") or {}).get("drug_name")
        ok = (
            actual_unit == case.get("expected_unit")
            and actual_route == case.get("expected_route")
            and all(term in answer for term in case.get("must_include", []))
            and all(term not in answer for term in case.get("forbid", []))
            and (
                "expected_card" not in case
                or actual_card_name == case["expected_card"]
            )
            and (
                "expected_next_drug" not in case
                or actual_next_drug_name == case["expected_next_drug"]
            )
            and (
                "expected_fallback_reason" not in case
                or result.get("fallback_reason") == case["expected_fallback_reason"]
            )
        )
        passed += int(ok)
        print(f"{'OK' if ok else 'XX'} 回归: {case['id']}")
        print(f"  匹配: {actual_unit} expected={case.get('expected_unit')} | 路由: {actual_route} expected={case.get('expected_route')}")
        if not ok:
            print(f"  答案: {answer}")
    return passed, len(cases)


def run_context_self_test(bank, cards, emb, variant_unit_ids, variant_vecs):
    """验证 OCR/手工纯药名切换后，省略药名的连续追问不会丢失上下文。"""
    current_drug = None
    cases = [
        ("阿莫西林胶囊", True, "阿莫西林胶囊", "drug_context_selected"),
        ("功效是什么", False, "阿莫西林胶囊", "what_treat"),
        ("作用是什么", False, "阿莫西林胶囊", "what_treat"),
        ("它的功效是什么", False, "阿莫西林胶囊", "what_treat"),
        ("他的功效是什么", False, "阿莫西林胶囊", "what_treat"),
        ("她有什么作用", False, "阿莫西林胶囊", "what_treat"),
        ("请问它有什么作用", False, "阿莫西林胶囊", "what_treat"),
        ("我想知道它的功效", False, "阿莫西林胶囊", "what_treat"),
        ("这个药有什么作用", False, "阿莫西林胶囊", "what_treat"),
        ("这药有什么作用", False, "阿莫西林胶囊", "what_treat"),
        ("这款药有什么功效", False, "阿莫西林胶囊", "what_treat"),
        ("此药有什么功效", False, "阿莫西林胶囊", "what_treat"),
        ("该药有什么功效", False, "阿莫西林胶囊", "what_treat"),
        ("刚才那个药有什么功效", False, "阿莫西林胶囊", "what_treat"),
        ("上面那个药有什么功效", False, "阿莫西林胶囊", "what_treat"),
        ("有什么用", False, "阿莫西林胶囊", "what_treat"),
        ("它的副作用是什么", False, "阿莫西林胶囊", "se_common"),
        ("蒙脱石散怎么冲", False, "蒙脱石散", "how_mix"),
        ("它怎么冲", False, "蒙脱石散", "how_mix"),
        ("维生素D滴剂", True, "维生素D滴剂", "drug_context_selected"),
        ("副作用是什么", False, "维生素D滴剂", "se_common"),
    ]
    passed = 0
    for question, expect_context_only, expected_drug, expected_intent in cases:
        result = answer_once(
            question, cards, current_drug, bank, emb, variant_unit_ids, variant_vecs,
            False, DEFAULT_MODEL,
        )
        current_drug = result.get("new_current_drug")
        actual_drug = result.get("card", {}).get("drug_name") if result.get("card") else None
        ok = (
            bool(result.get("context_only")) == expect_context_only
            and actual_drug == expected_drug
            and result.get("top_unit_id") == expected_intent
        )
        passed += int(ok)
        print(
            f"{'OK' if ok else 'XX'} {question} -> {actual_drug} / "
            f"{result.get('top_unit_id')} / context_only={result.get('context_only', False)}"
        )
    unrelated = answer_once(
        "今天天气怎么样",
        cards,
        current_drug,
        bank,
        emb,
        variant_unit_ids,
        variant_vecs,
        False,
        DEFAULT_MODEL,
    )
    current_drug = unrelated.get("new_current_drug")
    retained = answer_once(
        "它有什么作用",
        cards,
        current_drug,
        bank,
        emb,
        variant_unit_ids,
        variant_vecs,
        False,
        DEFAULT_MODEL,
    )
    retained_ok = (
        (retained.get("card") or {}).get("drug_name") == "维生素D滴剂"
        and retained.get("top_unit_id") == "what_treat"
    )
    passed += int(retained_ok)
    print(f"{'OK' if retained_ok else 'XX'} 无关问题后 -> 它有什么作用仍沿用维生素D滴剂")

    unlisted = answer_once(
        "阿托伐他汀片怎么吃",
        cards,
        current_drug,
        bank,
        emb,
        variant_unit_ids,
        variant_vecs,
        False,
        DEFAULT_MODEL,
    )
    unlisted_ok = (
        unlisted.get("card") is None
        and unlisted.get("new_current_drug") is None
        and unlisted.get("fallback_reason") == "unlisted_drug_reference"
    )
    passed += int(unlisted_ok)
    print(f"{'OK' if unlisted_ok else 'XX'} 阿托伐他汀片怎么吃 -> 未收录且清除上下文")

    unknown = answer_once(
        "阿托伐他汀怎么吃",
        cards,
        current_drug,
        bank,
        emb,
        variant_unit_ids,
        variant_vecs,
        False,
        DEFAULT_MODEL,
    )
    unknown_ok = (
        unknown.get("card") is None
        and unknown.get("new_current_drug") is None
        and unknown.get("fallback_reason") == "unknown_drug_reference"
    )
    passed += int(unknown_ok)
    print(f"{'OK' if unknown_ok else 'XX'} 阿托伐他汀怎么吃 -> 未识别且清除上下文")
    total = len(cases) + 3
    print(f"上下文测试结果: {passed}/{total}")
    return passed == total


def run_llm_smoke(bank, cards, emb, variant_unit_ids, variant_vecs, model, llm_backend=None, llm_url=None):
    questions = [
        "阿莫西林一般会产生什么副作用",
        "我现在感觉身体不是很舒服，阿莫西林导致的吗",
        "西替利嗪能和复方氨酚烷胺一起吗",
        "布洛芬吃了胃疼怎么办",
        "双歧杆菌能和阿莫西林一起吃吗",
        "氨氯地平血压正常了能停吗",
    ]
    current_drug = None
    for question in questions:
        result = answer_once(
            question,
            cards,
            current_drug,
            bank,
            emb,
            variant_unit_ids,
            variant_vecs,
            True,
            model,
            llm_backend,
            llm_url,
        )
        current_drug = result["new_current_drug"]
        print(f"\n你: {question}")
        if result["card"]:
            print(
                f"[{result['card']['drug_name']} | {result.get('top_unit_id')} "
                f"score={result.get('top_score', 0):.3f} | risk={result.get('risk_level')}]"
            )
            print(f"[原文]: {result.get('standard_answer')}")
            if result.get("fallback_reason") != "OK":
                print(f"[回退/质检]: {result.get('fallback_reason')}")
                if result.get("llm_raw"):
                    print(f"[LLM原始]: {result['llm_raw']}")
        print(f"助手: {result['answer']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", action="store_true", help="只输出卡片原文，不调用LLM。")
    parser.add_argument("--self-test", action="store_true", help="运行检索测试，不进入交互。")
    parser.add_argument("--context-self-test", action="store_true", help="运行纯药名上下文与连续追问测试。")
    parser.add_argument("--llm-smoke", action="store_true", help="运行少量LLM效果烟测，不进入交互。")
    parser.add_argument("--rebuild-index", action="store_true", help="强制重建并覆盖离线向量索引。")
    parser.add_argument(
        "--embed-backend",
        default=DEFAULT_EMBED_BACKEND,
        choices=["auto", "sentence-transformers", "onnx-fp32", "onnx-int8"],
        help="embedding后端；K1默认自动选择ONNX INT8。",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama模型名，默认 {DEFAULT_MODEL}")
    parser.add_argument(
        "--llm-backend",
        default=DEFAULT_LLM_BACKEND,
        choices=["ollama", "llama_cpp_completion", "llama_cpp_chat"],
        help="LLM后端：ollama 或 K1加速 llama.cpp API。",
    )
    parser.add_argument("--llm-url", default=DEFAULT_LLM_URL, help="LLM服务地址；留空则按后端使用默认地址。")
    args = parser.parse_args()

    print("加载通用问题银行和17张比赛药品卡片...")
    bank, cards = load_data()
    print("加载 embedding 模型...")
    emb = load_embedding_model(args.embed_backend)
    _, variant_unit_ids, variant_vecs, index_source = build_index(
        bank,
        emb,
        rebuild=args.rebuild_index,
        cards=cards,
    )

    print(
        f"就绪。问题银行: {len(bank['units'])}个意图, {len(variant_unit_ids)}条变体向量，"
        f"索引来源: {index_source}，后端: {emb.backend_name}。"
    )
    print(
        f"药品卡片: {len(cards)}张。模型: {args.model}。"
        f"LLM后端: {args.llm_backend} {args.llm_url or default_llm_url(args.llm_backend)}。"
        f"输出: {'原文直出' if args.raw else 'Qwen受控转述'}。"
    )

    if args.self_test:
        ok = run_self_test(bank, cards, emb, variant_unit_ids, variant_vecs)
        raise SystemExit(0 if ok else 1)
    if args.context_self_test:
        ok = run_context_self_test(bank, cards, emb, variant_unit_ids, variant_vecs)
        raise SystemExit(0 if ok else 1)
    if args.llm_smoke:
        run_llm_smoke(
            bank,
            cards,
            emb,
            variant_unit_ids,
            variant_vecs,
            args.model,
            args.llm_backend,
            args.llm_url or None,
        )
        return

    print("=" * 60)
    print("  输入问题开始对话，输入 q 退出")
    print("  可以先说药名，后面用“这个药”会沿用上一种药")
    print("=" * 60)

    current_drug = None
    while True:
        try:
            question = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not question:
            continue
        if question.lower() in {"q", "quit", "exit"}:
            print("再见！")
            break

        t0 = time.time()
        result = answer_once(
            question,
            cards,
            current_drug,
            bank,
            emb,
            variant_unit_ids,
            variant_vecs,
            not args.raw,
            args.model,
            args.llm_backend,
            args.llm_url or None,
        )
        current_drug = result["new_current_drug"]
        total_ms = (time.time() - t0) * 1000

        if result["card"] is None:
            print(f"助手: {result['answer']}")
            continue

        if result.get("context_only"):
            print(f"[已切换当前咨询药品: {result['card']['drug_name']} | 无需检索/LLM]")
            print(f"助手: {result['answer']}")
            continue

        timing = f"embedding:{result.get('embed_ms', 0):.0f}ms"
        if not args.raw:
            timing += f" LLM:{result.get('llm_ms', 0):.0f}ms"
        print(
            f"[药品: {result['card']['drug_name']} | 匹配: {result.get('top_unit_id')} "
            f"score={result.get('top_score', 0):.3f} risk={result.get('risk_level')} | "
            f"{timing} 总计:{total_ms:.0f}ms]"
        )
        if result.get("top_score", 0) < 0.72:
            print(f"[top3: {' | '.join(f'{uid}({score:.2f})' for uid, score in result.get('top3', []))}]")
        if result.get("standard_answer"):
            print(f"[原文({len(result['standard_answer'])}字)]: {result['standard_answer']}")
        if result.get("fallback_reason") not in {None, "OK", "raw"}:
            print(f"[质检回退: {result['fallback_reason']}]")
        print(f"助手: {result['answer']}")


if __name__ == "__main__":
    main()
