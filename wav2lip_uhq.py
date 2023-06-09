import os
import json
import requests
import base64
import io
import argparse
import numpy as np
from PIL import Image
import cv2
import dlib
from imutils import face_utils
import subprocess


def assure_path_exists(path):
    dir = os.path.dirname(path)
    if not os.path.exists(dir):
        os.makedirs(dir)


def get_framerate(video_file):
    video = cv2.VideoCapture(video_file)
    fps = video.get(cv2.CAP_PROP_FPS)
    video.release()
    return fps


def create_video_from_images(args, nb_frames):
    fps = str(get_framerate(args["input_file"]))
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", fps, "-start_number", "1", "-i", "output/output_%05d.png", "-vframes", str(nb_frames),
         "-b:v", "5000k", "output/video.avi"])


def extract_audio_from_video(args):
    subprocess.run(["ffmpeg", "-y", "-i", args["file"], "-vn", "-acodec", "copy", "output/output_audio.aac"])


def has_audio(video_file):
    output = subprocess.run(["ffmpeg", "-i", video_file], text=True, stderr=subprocess.PIPE)
    return "Audio:" in output.stderr


def add_audio_to_video(args):
    subprocess.run(["ffmpeg","-y",  "-i", "output/video.avi", "-i", "output/output_audio.aac", "-c:v", "copy", "-c:a", "aac", "-strict",
                    "experimental", "output/output_video.mp4"])


def create_image(image, mask, payload, shape, img_count):
    output_dir = 'output/'
    image = open(image, "rb").read()
    image_mask = open(mask, "rb").read()
    url = payload["url"]
    payload = payload["payload"]
    payload["init_images"] = ["data:image/png;base64," + base64.b64encode(image).decode('UTF-8')]
    payload["mask"] = "data:image/png;base64," + base64.b64encode(image_mask).decode('UTF-8')

    path = output_dir
    response = requests.post(url=f'{url}', json=payload)
    r = response.json()
    for idx in range(len(r['images'])):
        i = r['images'][idx]
        image = Image.open(io.BytesIO(base64.b64decode(i.split(",", 1)[0])))
        image_name = path + "output_" + str(img_count).rjust(5, '0') + ".png"
        image.save(image_name)


def initialize_dlib_predictor():
    print("[INFO] Loading the predictor...")
    detector = dlib.get_frontal_face_detector()
    predictor = dlib.shape_predictor("predicator/shape_predictor_68_face_landmarks.dat")
    return detector, predictor


def initialize_video_streams(args):
    print("[INFO] Loading File...")
    vs = cv2.VideoCapture(args["file"])
    vi = cv2.VideoCapture(args["input_file"])
    return vs, vi


def parse_arguments():
    ap = argparse.ArgumentParser()
    ap.add_argument("-f", "--file", required=True, type=str, default=-1, help="video generated by wav2lip path")
    ap.add_argument("-i", "--input_file", required=True, type=str, default=-1, help="original video path")
    ap.add_argument("-p", "--post_process", required=False, type=str, default="True", help="post processing to ControlNet")
    return vars(ap.parse_args())


def main():
    args = parse_arguments()
    output_dir = 'output/'
    assure_path_exists(output_dir)
    image_path = output_dir + "images/"
    assure_path_exists(image_path)
    mask_path = output_dir + "masks/"
    assure_path_exists(mask_path)

    with open("payloads/controlNet.json", "r") as f:
        payload = json.load(f)

    detector, predictor = initialize_dlib_predictor()
    vs, vi = initialize_video_streams(args)
    (mstart, mend) = face_utils.FACIAL_LANDMARKS_IDXS["mouth"]
    max_frame = str(int(vs.get(cv2.CAP_PROP_FRAME_COUNT)))
    frame_number = 0

    while True:
        image_name = image_path + 'image_' + str(frame_number).rjust(5, '0') + '.png'
        # print step of the process
        print("Processing frame: " + str(frame_number) + " of " + max_frame)
        image_name = "output/output_" + str(frame_number).rjust(5, '0') + ".png"
        if os.path.isfile(image_name):
            ret, frame = vs.read()
            ret, input_frame = vi.read()
            frame_number += 1
            continue

        ret, frame = vs.read()
        if not ret:
            break

        ret, input_frame = vi.read()
        if not ret:
            break

        img = frame
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        rects = detector(img, 1)
        mask = np.zeros_like(img)

        # Process each detected face
        result = input_frame
        for (i, rect) in enumerate(rects):
            shape = predictor(gray, rect)
            shape = face_utils.shape_to_np(shape)

            mouth = shape[mstart:mend]
            external_mouth_shape = mouth[:-7]

            kernel = np.ones((3, 3), np.uint8)
            mouth_mask = np.zeros_like(gray)
            cv2.fillConvexPoly(mouth_mask, external_mouth_shape, 255)
            mouth_dilated = cv2.dilate(mouth_mask, kernel, iterations=8)
            mouth_dilated_contour, _ = cv2.findContours(mouth_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            external_mouth_shape_extended = mouth_dilated_contour[0]

            cv2.fillConvexPoly(mask, np.array(external_mouth_shape_extended), (255, 255, 255))
            mask_blur = cv2.GaussianBlur(mask, (15, 15), 0)
            cv2.imwrite(mask_path + 'image_' + str(frame_number).rjust(5, '0') + '.png', mask_blur)

            mask_blur = mask_blur / 255
            dst = img * mask_blur
            result = result * (1 - mask_blur) + dst

            height, width, _ = result.shape
            image_name = image_path + 'image_' + str(frame_number).rjust(5, '0') + '.png'
            mask_name = mask_path + 'image_' + str(frame_number).rjust(5, '0') + '.png'
            cv2.imwrite(image_name, result)
            if args["post_process"] == "True":
                create_image(image_name, mask_name, payload, (width, height), frame_number)

        frame_number += 1

    cv2.destroyAllWindows()
    vs.release()
    vi.release()
    if args["post_process"] == "True":
        print("[INFO] Create Video output!")
        create_video_from_images(args, frame_number-1)
        if has_audio(args["file"]):
            print("[INFO] Extract Audio from input!")
            extract_audio_from_video(args)
            print("[INFO] Add Audio to Video!")
            add_audio_to_video(args)
        else:
            print("[INFO] No Audio in input file!")
            os.rename("output/video.avi", "output/video_output.mp4")

        print("[INFO] Done! file save in output/video_output.mp4")

if __name__ == "__main__":
    main()
