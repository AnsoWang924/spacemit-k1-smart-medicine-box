import cv2
import numpy as np
from PIL import Image


def sigmoid(x):
    return 1 / (1 + np.exp(-x))

def xywh2xyxy(x):
    # Convert nx4 boxes from [x, y, w, h] to [x1, y1, x2, y2] where xy1=top-left, xy2=bottom-right
    y = np.copy(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2  # top left x
    y[:, 1] = x[:, 1] - x[:, 3] / 2  # top left y
    y[:, 2] = x[:, 0] + x[:, 2] / 2  # bottom right x
    y[:, 3] = x[:, 1] + x[:, 3] / 2  # bottom right y
    return y

def letterbox(img, new_shape=(320, 320), color=(114, 114, 114), auto=False, scaleFill=False, scaleup=True):
    # Resize image to a 32-pixel-multiple rectangle https://github.com/ultralytics/yolov3/issues/232
    shape = img.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    # Scale ratio (new / old)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    if not scaleup:  # only scale down, do not scale up (for better test mAP)
        r = min(r, 1.0)

    # Compute padding
    ratio = r, r  # width, height ratios
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding
    if auto:  # minimum rectangle
        dw, dh = np.mod(dw, 64), np.mod(dh, 64)  # wh padding
    elif scaleFill:  # stretch
        dw, dh = 0.0, 0.0
        new_unpad = (new_shape[1], new_shape[0])
        ratio = new_shape[1] / shape[1], new_shape[0] / shape[0]  # width, height ratios

    dw /= 2  # divide padding into 2 sides
    dh /= 2

    if shape[::-1] != new_unpad:  # resize
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)  # add border
    return img, ratio, (dw, dh)

def det_data_process(img0):
    img = letterbox(img0)[0]
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = img.transpose(2, 0, 1).copy()

    img = (img / 255.0).astype(np.float32)  # 0 - 255 to 0.0 - 1.0
    img = img[None]
    return img

#######################################################################
def preprocess_input(image):
    image = (image / 255.0 - 0.5) / 0.5     
    return image

def rec_data_process(img):    
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(img)
    new_im = np.array(resize_image(img), np.float32)
    new_im = preprocess_input(new_im)
    new_im = np.transpose(new_im, (2, 0, 1))
    return new_im[None]

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

def nms(bboxes,scores,threshold=0.5):
    x1 = bboxes[:,0]
    y1 = bboxes[:,1]
    x2 = bboxes[:,2]
    y2 = bboxes[:,3]
    areas = (x2-x1) * (y2-y1)
    order = np.argsort(scores)[::-1]
    keep = []
    while len(order)>0:
        i = order[0]
        keep.append(i)
        if len(order)==1:
            break

        xx1 = x1[order[1:]].clip(min=x1[i])
        yy1 = y1[order[1:]].clip(min=y1[i])
        xx2 = x2[order[1:]].clip(max=x2[i])
        yy2 = y2[order[1:]].clip(max=y2[i])

        w = (xx2-xx1).clip(min=0)
        h = (yy2-yy1).clip(min=0)
        inter = w*h

        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        ids = (ovr<=threshold).nonzero()[0]
        if len(ids) == 0:
            break
        order = order[ids+1]
    return np.array(keep)

def _make_grid(nx=20, ny=20):
    xv, yv = np.meshgrid(np.arange(ny), np.arange(nx))
    return np.stack((xv, yv), 2).reshape((1, 1, ny, nx, 2)).astype(np.float32)

def scale_coords(img1_shape, coords, img0_shape, ratio_pad=None):
    # Rescale coords (xyxy) from img1_shape to img0_shape
    if ratio_pad is None:  # calculate from img0_shape
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])  # gain  = old / new
        pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
    else:
        gain = ratio_pad[0][0]
        pad = ratio_pad[1]

    coords[[0, 2]] -= pad[0]  # x padding
    coords[[1, 3]] -= pad[1]  # y padding
    coords[:4] /= gain
    clip_coords(coords, img0_shape)
    return coords

def clip_coords(boxes, img_shape):
    # Clip bounding xyxy bounding boxes to image shape (height, width)
    boxes[0] = np.clip(boxes[0], 0, img_shape[1])  # x1
    boxes[1] = np.clip(boxes[1], 0, img_shape[0])  # y1
    boxes[2] = np.clip(boxes[2], 0, img_shape[1])  # x2
    boxes[3] = np.clip(boxes[3], 0, img_shape[0])  # y2
