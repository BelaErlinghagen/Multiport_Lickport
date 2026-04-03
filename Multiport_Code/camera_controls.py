import time
from pypylon import pylon
import numpy as np

class TrackingCamera:
    def __init__(self, shared_image):
        print("Initializing Camera.")
        self.camera = pylon.InstantCamera(
            pylon.TlFactory.GetInstance().CreateFirstDevice()
        )
        self.shared_image = shared_image
        self.camera_on = False
        time.sleep(2)
        print("Camera is ready.")

    def run(self):
        self.camera.Open()
        self.camera_on = True
        numberOfImagesToGrab = 100
        self.camera.StartGrabbingMax(numberOfImagesToGrab)

        while self.camera.IsGrabbing() and self.camera_on:
            grabResult = self.camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grabResult.GrabSucceeded():
                img = grabResult.Array.astype(np.float32).flatten()
                self.shared_image[:] = img
            grabResult.Release()

        self.camera_on = False
        self.camera.Close()

    def stop(self):
        self.camera_on = False