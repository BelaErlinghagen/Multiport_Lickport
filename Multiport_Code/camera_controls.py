import time
from pypylon import pylon
from shared_states import IMG_HEIGHT, IMG_WIDTH
import numpy as np

class TrackingCamera:
    def __init__(self, frame_queue, dlc_queue, plotting_shape):
        print("Initializing Camera.")
        self.cam = pylon.InstantCamera(
            pylon.TlFactory.GetInstance().CreateFirstDevice()
        )
        self.shared_image_queue = frame_queue
        self.dlc_input = dlc_queue
        self.shape = plotting_shape
        time.sleep(2)
        print("Camera is ready.")

    def run(self, running_flag):
        self.cam.Open()
        self.camera_on = True
        self.cam.Gain.SetValue(10.0)
        self.cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

        while self.cam.IsGrabbing() and running_flag.value:
            grabResult = self.cam.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
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
                if self.shared_image_queue.full():
                    try:
                        self.shared_image_queue.get_nowait()
                    except:pass
                self.shared_image_queue.put(downsampled.astype(np.uint8))
                #print(np.mean(downsampled))
            grabResult.Release()
            time.sleep(0.01)

        self.cam.Close()

    def stop(self):
        self.camera_on = False

def camera_process(shared_image, dlc_queue, cam_shape, running_flag):
    camera = TrackingCamera(shared_image, dlc_queue, cam_shape)
    camera.run(running_flag)

if __name__ == "__main__":
    from multiprocessing import Queue, Array, Value
    cam_shape = (200, 200)
    cam_size = int(np.prod(cam_shape))
    dlc_queue = Queue(maxsize=2)
    camera_running = Value('b', True)  
    frame_queue = Queue(maxsize=2)
    sensor_array = Array('i', 16)    
    camera_process(frame_queue, dlc_queue, cam_shape, camera_running)