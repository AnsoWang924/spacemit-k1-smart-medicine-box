import cv2
import numpy as np
import argparse

from base.detection import Detection
from base.recognition import Recognition
import time
import queue
import threading    

def face_detection_and_recognition(frame_queue, result_queue):
    det_model_path = "onnx_model/yolov5n-face_320_cut.q.onnx"
    rec_model_path = "onnx_model/arcface_mobilefacenet_cut.q.onnx"
    faces_path = "faces"

    
    det = Detection(det_model_path)
    rec = Recognition(rec_model_path,faces_path)


    while True:        
        try:
            frame = frame_queue.get()

            face_imgs,boxes = det.infer_face(frame)
            results = {}
            if face_imgs is not None:
                for i, (face_img, box) in enumerate(zip(face_imgs, boxes)):                    
                    face_vector = rec.infer(face_img)

                    face_name = None
                    max_similarity_score = 0.0

                    for key, value in rec.face_bank.items():
                        similarity_scores = face_vector @ value.T
                        if similarity_scores[0][0] > max_similarity_score and similarity_scores[0][0] > 0.6:
                            max_similarity_score = similarity_scores[0][0]
                            face_name = key
                            results[face_name] = box

                        else:
                            results[f"unknown_{i}"] = box
                        

            if results:
                while not result_queue.empty():
                    result_queue.get()  
                result_queue.put(results)

        except queue.Empty:
            continue

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--use_single_thread', action='store_true', help='use single thread')
    args = parser.parse_args()

    
   
    try:
        if not args.use_single_thread:
            cap = cv2.VideoCapture(20)
            frame_queue = queue.Queue()
            result_queue = queue.Queue()    

            thread = threading.Thread(target=face_detection_and_recognition, args=(frame_queue, result_queue),daemon=True)
            thread.start()

            last_detection_time = 0  # 记录上一次检测到人脸的时间
            extended_display_duration = 2  # 延长显示的时间（秒）
            normal_delay = 1  # 正常帧处理延迟（毫秒）
            extended_delay = 300  # 延长显示时的帧处理延迟（毫秒）

            while True:            
                ret, frame = cap.read()
                if not ret:
                    break

                while not frame_queue.empty():
                    frame_queue.get()  

                frame_queue.put(frame)

                if not result_queue.empty():
                    reusults = result_queue.get()        
                    for key,value in reusults.items():
                        if "unknown" not in key:
                            cv2.putText(frame, key, (int(value[0]), int(value[1] - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)                          
                            last_detection_time = time.time()
                        cv2.rectangle(frame, (int(value[0]), int(value[1])), (int(value[2]), int(value[3])), (0, 255, 0), 2)
                            
                cv2.imshow("frame", frame)        
            
                current_time = time.time()
                if current_time - last_detection_time < extended_display_duration:
                    # 如果在延长显示时间内，降低帧率
                    cv2.waitKey(extended_delay)
                else:
                    # 否则使用正常帧率
                    cv2.waitKey(normal_delay)
            
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

        else:
            det_model_path = "onnx_model/yolov5n-face_320_cut.q.onnx"
            rec_model_path = "onnx_model/arcface_mobilefacenet_cut.q.onnx"
            faces_path = "faces"

            
            det = Detection(det_model_path)
            rec = Recognition(rec_model_path,faces_path)

            
            while True:        
                ret, frame = cap.read()
                fram_cp = frame.copy()
                if not ret: 
                    break
                face_imgs,boxes = det.infer_face(fram_cp)
                results = {}
                if len(face_imgs) != 0:                
                    for i, (face_img, box) in enumerate(zip(face_imgs, boxes)):
                        
                        face_vector = rec.infer(face_img)

                        face_name = None
                        max_similarity_score = 0.0

                        for key, value in rec.face_bank.items():
                            similarity_scores = face_vector @ value.T
                            if similarity_scores[0][0] > max_similarity_score and similarity_scores[0][0] > 0.6:
                                max_similarity_score = similarity_scores[0][0]
                                face_name = key
                                results[face_name] = box
                            else:
                                results[f"unknown_{i}"] = box
                for key,value in results.items():
                    if "unknown" not in key:
                        cv2.putText(frame, key, (int(value[0]), int(value[1] - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                        
                    cv2.rectangle(frame, (int(value[0]), int(value[1])), (int(value[2]), int(value[3])), (0, 255, 0), 2)

                cv2.imshow("frame", frame)        
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break


    finally:
        cap.release()
        cv2.destroyAllWindows()























if __name__ == '__main__':
    main()