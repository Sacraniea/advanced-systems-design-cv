"""
predict - YOLO object detection to find gauges and valves in images


"""

from ultralytics import YOLO
from annotater import Annotater
import cv2
from datetime import datetime, timedelta


class Predictor(Annotater):
    """Predictor class for YOLO object detection to find gauges and valves in images."""

    _instance = None  # Class-level variable to store the single instance
        
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            # Create the instance if it doesn't exist yet
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, model_location: str):
        super().__init__()
        self.model = YOLO(model_location)
        self.latest_prediction = []
        self.time_last_gauge = None
        self.latest_gauge_image = None
        
        
    def predict(self, raw_image):
        image = raw_image.copy()
        boxes = self.model.predict(image, verbose=False)[0].boxes
        cleaned_prediction = [
            (self.class_names[int(cls)], tuple(xywhn))
            for cls, xywhn in zip(boxes.cls.tolist(), boxes.xywhn.tolist())
        ]
        self.__cleanup_post_prediction(cleaned_prediction, image)
        
        return cleaned_prediction
    
    def get_latest_gauge_crop(self):
        # if last gauge was found within 30 seconds
        if (datetime.now() - self.time_last_gauge) < timedelta(seconds=30):
            return self.latest_gauge_image
        else:
            return None
        
    
    def demonstrate(self, image):
        results = self.predict(image)
        annotated_image = self.annotate_image(image, results)
        cv2.imshow(annotated_image)
        
    ####### PRIVATE FUNCTIONS #################
        
    def __cleanup_post_prediction(self, cleaned_prediction, image):
        # MUST NOT EDIT cleaned_prediction
        self.latest_prediction = cleaned_prediction
        
        for name, crop in cleaned_prediction:
            if name == "gauge":
                self.__update_gauge_image_from_crop(image, crop)
                self.time_last_gauge = datetime.now()
                
    def __update_gauge_image_from_crop(self, image, gauge_crop):
        x1, y1, x2, y2 = self.__to_pixel_box(image, gauge_crop)
        self.latest_gauge_image = image[y1:y2, x1:x2].copy()
        
    
    def __to_pixel_box(self, image, box):
        xc, yc, w, h = map(float, box)
        H, W = image.shape[:2]
        x1, y1 = max(int((xc - w / 2) * W), 0), max(int((yc - h / 2) * H), 0)
        x2, y2 = min(int((xc + w / 2) * W), W), min(int((yc + h / 2) * H), H)
        return x1, y1, x2, y2
            

        
        
        
