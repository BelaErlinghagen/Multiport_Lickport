from pypylon import pylon
from dlclive import DLCLive, Processor
from threading import Thread
import numpy as np
import time

class TrackingCamera:
    def __init__(self, shared_image):
        print("Initializing Camera.")
        self.camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
        self.shared_image = shared_image
        self.camera_on = False
        self.deeplabcut_processing = Processor()
        self.deeplabcut_live = "test" # DLCLive(<path to exported model directory>, processor=self.deeplabcut_processing)
        self.camera_process = Thread(target=self.retrieve_images, args=([self.camera,]))
        self.camera_process.start()
        self.dlc = Thread(target=self.deeplabcut_tracking, args = ([self.deeplabcut_live,]))
        self.dlc.start()
        time.sleep(2)
        print("Camera is fetching images.")
        
        
    def retrieve_images(self, camera):
        camera.Open()
        self.camera_on = True
        numberOfImagesToGrab = 100
        camera.StartGrabbingMax(numberOfImagesToGrab)
        while camera.IsGrabbing() and self.camera_on:
            grabResult = camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grabResult.GrabSucceeded():
                self.shared_image[:] = grabResult.Array.astype(np.float32)
            grabResult.Release()
        self.camera_on = False
        camera.Close()
    
    def deeplabcut_tracking(self, dlc_live):
        image = self.shared_image
        #dlc_live.init_inference(image)
        #dlc_live.get_pose(image)

    def end_processes(self):
        self.camera_process.join()
        self.dlc.join()
        