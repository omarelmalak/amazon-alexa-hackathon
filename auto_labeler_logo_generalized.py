import os
import shutil
import torch
import cv2
import argparse
import groundingdino
import torchvision
from groundingdino.util.inference import load_model, load_image, predict, annotate

# 1. Configuration
CONFIG_PATH_LOW = os.path.join(os.path.dirname(groundingdino.__file__), "config", "GroundingDINO_SwinT_OGC.py")
CONFIG_PATH_HIGH = os.path.join(os.path.dirname(groundingdino.__file__), "config", "GroundingDINO_SwinB_cfg.py")

WEIGHTS_PATH_LOW = "weights/groundingdino_swint_ogc.pth"
WEIGHTS_PATH_HIGH = "weights/groundingdino_swinb_cogcoor.pth"
FRAMES_DIR = "data/input/logos/"
LABEL_DIR = "data/output/txt_labels/"
VISUAL_DIR = "data/output/annotated_frames/"

CLASS_MAPPING = {
    "logo": 0,
    "text": 0,
}

DISTRACTORS = []

TEXT_PROMPT = " . ".join(list(CLASS_MAPPING.keys()) + DISTRACTORS) + " ."

# 3. Thresholds and Size Filters
BOX_THRESHOLD = 0.26
TEXT_THRESHOLD = 0.27

MIN_BOX_WIDTH = 0.015
MIN_BOX_HEIGHT = 0.015

MAX_BOX_WIDTH = 0.95   # Allow large foreground figures
MAX_BOX_HEIGHT = 0.95  # Allow tall close-up figures


os.makedirs(LABEL_DIR, exist_ok=True)
os.makedirs(VISUAL_DIR, exist_ok=True)

def resolve_class_id(detected_phrase, class_map):
    cleaned = detected_phrase.strip().lower()
    brand_keywords = {
        "logo": 0,
        "text": 0
    }
    for kw, cid in brand_keywords.items():
        if kw in cleaned:
            return cid, kw
    return None, None


def auto_label_frames(frames_subdir, model_strength):
    clear_existing_annotations()

    if model_strength == 'high':
        print("USING SWIN-B (HIGH)")
        model = load_model(CONFIG_PATH_HIGH, WEIGHTS_PATH_HIGH, device="cpu")
    else:
        print("USING SWIN-T (LOW)")
        model = load_model(CONFIG_PATH_LOW, WEIGHTS_PATH_LOW, device="cpu")

    # 3. Process extracted frames
    frames_subdir_path = f"{FRAMES_DIR}{frames_subdir}"
    for filename in os.listdir(frames_subdir_path):
        if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
            continue

        img_path = os.path.join(frames_subdir_path, filename)
        image_source, image = load_image(img_path)

        boxes, logits, phrases = predict(
            model=model,
            image=image,
            caption=TEXT_PROMPT,
            box_threshold=BOX_THRESHOLD,
            text_threshold=TEXT_THRESHOLD,
            device="cpu"
        )

        print(f"\nFile: {filename}")
        print(f"Raw DINO Phrases: {phrases} | Confidence: {logits}")

        yolo_lines = []

        # Track valid detections to draw on the visual output
        filtered_boxes = []
        filtered_logits = []
        filtered_phrases = []

        for box, logit, phrase in zip(boxes, logits, phrases):
            box_w, box_h = box[2].item(), box[3].item()

            # Discard tiny artifacts AND massive screen-sized boxes
            if (box_w < MIN_BOX_WIDTH or box_h < MIN_BOX_HEIGHT or
                    box_w > MAX_BOX_WIDTH or box_h > MAX_BOX_HEIGHT):
                continue

            class_id, class_name = resolve_class_id(phrase, CLASS_MAPPING)
            if class_id is not None:
                cx, cy = box[0].item(), box[1].item()
                yolo_lines.append(f"{class_id} {cx:.6f} {cy:.6f} {box_w:.6f} {box_h:.6f}\n")

                # Store passing data for visualization
                filtered_boxes.append(box)
                filtered_logits.append(logit)
                filtered_phrases.append(class_name)

        txt_filename = os.path.splitext(filename)[0] + ".txt"
        output_txt_dir_path = f"{LABEL_DIR}{frames_subdir}"
        txt_path = os.path.join(output_txt_dir_path, txt_filename)
        output_visual_dir_path = f"{VISUAL_DIR}{frames_subdir}"
        visual_path = os.path.join(output_visual_dir_path, filename)

        os.makedirs(output_txt_dir_path, exist_ok=True)
        os.makedirs(output_visual_dir_path, exist_ok=True)

        if yolo_lines:
            # 1. Convert to tensors for NMS
            viz_boxes = torch.stack(filtered_boxes)
            viz_logits = torch.stack(filtered_logits)

            # 2. Convert center [cx, cy, w, h] to corners [x1, y1, x2, y2] for NMS
            box_corners = torchvision.ops.box_convert(viz_boxes, in_fmt="cxcywh", out_fmt="xyxy")

            # 3. Apply NMS (keep boxes that don't overlap heavily, IoU > 0.5)
            keep_indices = torchvision.ops.nms(box_corners, viz_logits, iou_threshold=0.5)

            # 4. Filter your lists based on NMS results
            final_yolo_lines = [yolo_lines[i] for i in keep_indices]
            final_boxes = viz_boxes[keep_indices]
            final_logits = viz_logits[keep_indices]
            final_phrases = [filtered_phrases[i] for i in keep_indices]

            # 5. Write the NMS-filtered YOLO lines to the text file
            with open(txt_path, "w") as f:
                f.writelines(final_yolo_lines)

            # 6. Create a BGR copy of the original image for OpenCV to draw on
            annotated_frame = cv2.cvtColor(image_source.copy(), cv2.COLOR_RGB2BGR)
            img_h, img_w, _ = annotated_frame.shape

            # 7. Draw custom bounding boxes using the NMS-filtered data
            for box, logit, class_name in zip(final_boxes, final_logits, final_phrases):
                cx, cy, w, h = box[0].item(), box[1].item(), box[2].item(), box[3].item()

                # Convert normalized [cx, cy, w, h] to absolute [x1, y1, x2, y2]
                x_min = int((cx - w / 2) * img_w)
                y_min = int((cy - h / 2) * img_h)
                x_max = int((cx + w / 2) * img_w)
                y_max = int((cy + h / 2) * img_h)

                # Draw the bounding box (Green, thickness 2)
                cv2.rectangle(annotated_frame, (x_min, y_min), (x_max, y_max), (0, 255, 0), 2)

                # Draw the text label
                label = f"{class_name} {logit.item():.2f}"
                font_scale = 0.4
                font_thickness = 1

                # Create a small black background for the text so it's readable
                (text_width, text_height), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                                                               font_thickness)
                cv2.rectangle(annotated_frame, (x_min, y_min - text_height - 6), (x_min + text_width, y_min), (0, 0, 0),
                              -1)

                # Add white text on top
                cv2.putText(annotated_frame, label, (x_min, y_min - 3), cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                            (255, 255, 255), font_thickness)

            cv2.imwrite(visual_path, annotated_frame)
            print(f"Annotated {filename} with {len(final_yolo_lines)} detections. Visual saved.")
        else:
            open(txt_path, "w").close()

            # For negative samples, convert to BGR before saving
            blank_frame = cv2.cvtColor(image_source, cv2.COLOR_RGB2BGR)
            cv2.imwrite(visual_path, blank_frame)

            print(f"Generated negative sample for {filename}.")

def clear_existing_annotations():
    for directory in [LABEL_DIR, VISUAL_DIR]:
        if os.path.exists(directory):
            print(f"Clearing old files from {directory}...")
            shutil.rmtree(directory) # Deletes the folder and everything inside it
        os.makedirs(directory, exist_ok=True) # Recreates it completely empty

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Use Grounding DINO with labels + prompts to build a rough annotated dataset."
    )
    parser.add_argument(
        "--directory",
        type=str,
        required=True,
        help="Name of the source match video frames directory.",
    )

    parser.add_argument(
        "--model",
        type=str,
        required=True,
        help="Model strength ('high' uses swin-b and 'low' uses swin-t).",
    )

    args = parser.parse_args()

    auto_label_frames(
        frames_subdir=args.directory,
        model_strength=args.model,
    )
