import cv2
import numpy as np
import onnxruntime as ort
#import spacemit_ort
import os
from PIL import Image

class Recognition:
    def __init__(self, model_path,face_bank_path):
        self.sess = ort.InferenceSession(
        model_path,
        providers=["CPUExecutionProvider"],
        sess_options=None
        )
        self.face_bank = self.init_face_bank(face_bank_path)

    def infer(self, img_src):
        img = self.process(img_src)
        face_vector = self.to_e(self.sess.run(None, {"images": img}))
        return face_vector

    def init_face_bank(self, face_bank_path):
        npy_dict = {}
        for filename in os.listdir(face_bank_path):
            if filename.endswith(".npy"):            
                key = os.path.splitext(filename)[0]                
                filepath = os.path.join(face_bank_path, filename)
                npy_dict[key] = np.load(filepath)
        return npy_dict

    def process(self, img):
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(img)
        new_im = np.array(self.resize_image(img), np.float32)
        new_im = (new_im / 255.0 - 0.5) / 0.5     
        new_im = np.transpose(new_im, (2, 0, 1))

        return new_im[None]
    
    @staticmethod
    def resize_image(image, size=[112, 112], letterbox_image=True):
        iw, ih  = image.size
        w, h    = size
        if letterbox_image:
            scale   = min(w/iw, h/ih)
            nw      = int(iw*scale)
            nh      = int(ih*scale)

            image   = image.resize((nw,nh), Image.BICUBIC)
            new_image = Image.new('RGB', size, (128,128,128))
            new_image.paste(image, ((w-nw)//2, (h-nh)//2))
        else:
            new_image = image.resize((w, h), Image.BICUBIC)
        return new_image
    
    @staticmethod
    def to_e(data_list):
        data = data_list[0]
        vector_e = data / np.sqrt(np.sum(np.power(data, 2)))
        return vector_e