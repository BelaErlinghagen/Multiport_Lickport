from pypylon import pylon
from threading import Thread
import numpy as np
import time

class TrackingCamera:
    def __init__(self):
        print("Initializing Camera.")
        self.camera_on = False
        self.camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
        self.current_image = np.array([])
        self.camera_process = Thread(target=self.retrieve_images, args=([self.camera,]))
        self.camera_process.start()
        time.sleep(2)
        print("Camera is fetching images.")
        
        
    def retrieve_images(self, camera):
        camera.Open()
        numberOfImagesToGrab = 100
        camera.StartGrabbingMax(numberOfImagesToGrab)
        self.camera_on = True
        while camera.IsGrabbing():
            grabResult = camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grabResult.GrabSucceeded():
                self.current_image = grabResult.Array
            grabResult.Release()
        camera.Close()
        self.camera_on = False


if __name__ == "__main__":
    camera = TrackingCamera()
    while  camera.camera_on:
        print(camera.current_image)