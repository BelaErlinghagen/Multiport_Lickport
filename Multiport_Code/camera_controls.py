import time
from pypylon import pylon
from shared_states import IMG_HEIGHT, IMG_WIDTH

class TrackingCamera:
    def __init__(self, shared_image, dlc_queue, plotting_shape):
        print("Initializing Camera.")
        self.camera = pylon.InstantCamera(
            pylon.TlFactory.GetInstance().CreateFirstDevice()
        )
        self.shared_image = shared_image
        self.dlc_input = dlc_queue
        self.shape = plotting_shape
        self.camera_on = False
        time.sleep(2)
        print("Camera is ready.")

    def run(self):
        self.camera.Open()
        self.camera_on = True
        self.camera.Gain.SetValue(48.0)
        self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

        while self.camera.IsGrabbing() and self.camera_on:
            grabResult = self.camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grabResult.GrabSucceeded():
                img = grabResult.Array
                start_x = (IMG_WIDTH - IMG_HEIGHT) // 2
                end_x = start_x + IMG_HEIGHT
                cropped_img = img[:, start_x:end_x]
                # send full quality cropped image to dlc
                if self.dlc_input.full():
                    self.dlc_input.get()
                self.dlc_input.put(cropped_img)
                # downsample image for camera plotting
                target_h, target_w = self.shape
                step_h = IMG_HEIGHT // target_h
                step_w = IMG_HEIGHT // target_w
                downsampled = cropped_img[::step_h, ::step_w][:target_h, :target_w]
                print(downsampled)
                self.shared_image[:] = downsampled.flatten()
            grabResult.Release()

        self.camera_on = False
        self.camera.Close()

    def stop(self):
        self.camera_on = False

if __name__ == "__main__":
    from multiprocessing import Queue, Array
    import numpy as np
    cam_shape = (400, 400)
    cam_size = int(np.prod(cam_shape))
    dlc_queue = Queue()
    shared_image = Array('f', cam_size) 
    camera = TrackingCamera(shared_image, dlc_queue, cam_shape)
    camera.run()