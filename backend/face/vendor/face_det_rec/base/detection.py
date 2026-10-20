import cv2
import numpy as np
import onnxruntime as ort
#import spacemit_ort
from .utils import letterbox,sigmoid,xywh2xyxy,nms,scale_coords,_make_grid
import time

class Detection:
    def __init__(self, model_path, conf_thres=0.25):        
        self.conf_thres = conf_thres
        self.sess = ort.InferenceSession(
        model_path,
        providers=["CPUExecutionProvider"],
        sess_options=None
        )

    def infer(self, img_src):
        img = img_src.copy()
        img = self.process_img(img)
        model_infer = self.sess.run(None, {"images": img})
        pred = self.post_process(model_infer)
        pred = self.non_max_suppression_face(pred, 0.6, 0.5)

        face_im = None
        for i, det in enumerate(pred):
            if len(det):
                det[:, :4] = scale_coords(img.shape[2:], det[:, :4], img_src.shape).round()
                xyxy = det[0, :4]
                face_im = img_src[int(xyxy[1]):int(xyxy[3]), int(xyxy[0]):int(xyxy[2])]
        return face_im            


    def infer_face(self, img_src):
        img = img_src.copy()
        img = self.process_img(img)
        model_infer = self.sess.run(None, {"images": img})
        pred = self.post_process(model_infer)        
        pred = self.non_max_suppression_face(pred, 0.6, 0.5)
        
        
        face_im = []
        boxes = []
        if pred is not None:                                
            for i, det in enumerate(pred[0]):
                if len(det):
                    det[:4] = scale_coords(img.shape[2:], det[:4], img_src.shape).round()
                    xyxy = det[:4]
                    boxes.append(xyxy)
                    face_im.append(img_src[int(xyxy[1]):int(xyxy[3]), int(xyxy[0]):int(xyxy[2])])
                            
            return face_im,boxes
        else:
            return None,None


    def process_img(self, img):
        img = letterbox(img)[0]
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.transpose(2, 0, 1).copy()

        img = (img / 255.0).astype(np.float32)  # 0 - 255 to 0.0 - 1.0
        img = img[None]
        return img

    def post_process(self, output):
        init_anchors = np.array([4.,5.,8.,10.,13.,16.,23.,29.,
                                43.,55.,73.,105.,146.,217.,
                                231.,300.,335.,433.]).astype(np.float32).reshape(3,1,3,1,1,2)
        strides = [8, 16, 32]
        pred_list = []
        for idx, pred in enumerate(output):
            y = np.full_like(pred[..., :6], 0)
            bs, _, ny, nx, _ = pred.shape
            grid = _make_grid(nx, ny)
    
            y_tmp = sigmoid(np.concatenate([pred[..., :5], pred[..., 15:]], axis=-1))
            y[..., :2] = (y_tmp[..., :2] * 2.-0.5+grid) * strides[idx]
            y[..., 2:4] = (y_tmp[..., 2:4] * 2) ** 2 * init_anchors[idx]
            y[..., 4:] = y_tmp[..., 4:]
            pred_list.append(y.reshape(bs, -1, 6))
        pred_result = np.concatenate(pred_list, 1)
        return pred_result

    @staticmethod
    def non_max_suppression_face(prediction, conf_thres=0.25, iou_thres=0.45, classes=None, agnostic=False, labels=()):
        nc = prediction.shape[2] - 5  # number of classes
        xc = prediction[..., 4] > conf_thres  # candidates

        # Settings
        min_wh, max_wh = 2, 4096  # (pixels) minimum and maximum box width and height
        time_limit = 10.0  # seconds to quit after

        t = time.time()
        output = [np.zeros((0, 6))] * prediction.shape[0]
        for xi, x in enumerate(prediction):  # image index, image inference
            x = x[xc[xi]]  # confidence
            # Cat apriori labels if autolabelling
            if labels and len(labels[xi]):
                l = labels[xi]
                v = np.zeros((len(l), nc + 5), device=x.device)
                v[:, :4] = l[:, 1:5]  # box
                v[:, 4] = 1.0  # conf
                v[range(len(l)), l[:, 0].long() + 5] = 1.0  # cls
                x = np.concatenate((x, v), 0)
            # If none remain process next image
            if not x.shape[0]:
                continue

            # Compute conf
            x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf

            box = xywh2xyxy(x[:, :4])
            conf = x[:, 5:].max(1).reshape(-1, 1)
            class_idx = np.argmax(x[:, 5:], axis=1).reshape(-1, 1).astype(np.float32)
            x = np.concatenate((box, conf, class_idx), 1)[conf.reshape(-1) > conf_thres]

            # If none remain process next image
            n = x.shape[0]  # number of boxes
            if not n:
                continue

            # Batched NMS
            c = x[:, 5:6] * (0 if agnostic else max_wh)  # classes
            boxes, scores = x[:, :4] + c, x[:, 4]  # boxes (offset by class), scores
            i = nms(boxes, scores, iou_thres)  # NMS

            output[xi] = x[i]
            if (time.time() - t) > time_limit:
                break  # time limit exceeded

            return output
        
        
        
