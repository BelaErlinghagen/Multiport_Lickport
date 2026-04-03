from pypylon import pylon
from multiprocessing import shared_memory
import numpy as np
import time
import shared_states as s

class TrackingCamera:
    def __init__(self, shared_image):
        print("Initializing Camera.")
        self.camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
        self.shared_image = shared_image
        self.camera_on = False
        time.sleep(2)
        print("Camera is fetching images.")
        
        
    def run(self):
        self.camera.Open()
        self.camera_on = True
        numberOfImagesToGrab = 100
        self.camera.StartGrabbingMax(numberOfImagesToGrab)
        while self.camera.IsGrabbing() and self.camera_on:
            grabResult = self.camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grabResult.GrabSucceeded():
                frame = grabResult.Array
                h, w = frame.shape
                # Compute center crop
                start_x = (w - h) // 2
                end_x = start_x + h
                cropped = frame[:, start_x:end_x]
                self.shared_image[:] = cropped.astype(np.float32)
            grabResult.Release()
        self.camera_on = False
        self.camera.Close()
    
    def stop(self):
        self.camera_on = False
        