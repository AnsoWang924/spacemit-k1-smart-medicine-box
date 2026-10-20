import time
import os

from .models.sensevoice_bin import SenseVoiceSmall
from .models.postprocess_utils import rich_transcription_postprocess


class llm_asr:
    def __init__(self, sensevoice_path, intra_op_num_threads=4, **kwargs):
        self.sense_model = SenseVoiceSmall(
            sensevoice_path,
            batch_size=10,
            quantize=True,
            intra_op_num_threads=intra_op_num_threads,
            **kwargs,
        ) ## 里面还包含其它参数，可以在本地去查看其他参数的使用或者直接前往官方github页面查看相应内容
        print("Initial sense model successfully!")
  
    def audio_to_text(self, wav_path):
        # 开始计时
        start_time = time.perf_counter()
        if wav_path is None:
            print("未进行录音。")

        # 将音频文件传递给 ASR 模型            
        wav_or_scp = [wav_path]
        res = self.sense_model(wav_or_scp, language="zh", use_itn=True)
        res = res[0][0].tolist()
        text = rich_transcription_postprocess(res[0])
        print("语音转文字:", text)
        # 删除临时文件
        os.remove(wav_path)

        print()
        # 结束计时
        end_time = time.perf_counter()

        # 计算耗时
        elapsed_time = end_time - start_time

        # 输出耗时
        print(f"Elapsed time: {elapsed_time:.6f} seconds")
        return text
