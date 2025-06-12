import torch
import torch.nn.functional as F
import numpy as np
import tempfile
import time
import os
import gc
from comfy.utils import ProgressBar
from ..utils.colored_print import color, style

# ComfyUI nodes for creating stereoscopic 3D side-by-side (SBS) images and videos.
# 
# Generates 3D effects by horizontally shifting pixels based on depth maps,
# where brighter areas in the depth map appear closer and darker areas appear further away from viewer.

# The amount of shift is controlled by the depth_scale parameter. The higher the value the more exaggerated
# the illusion of depth. It depends on the image's content.  Close-ups tend to benefit from higher values.
# 
# ┌───────────────────┬───────────────────────────────────────────────────────────────────────────────┐
# │ Depth Scale Value │                                Effect                                         |
# ├───────────────────┼───────────────────────────────────────────────────────────────────────────────|
# │ 10–20             │ Subtle stereo depth.       |
# │ 21–50             │ Balanced depth — good general-purpose range. Enough pop without distortion.   |
# │ 50–100            │ Strong depth — works with clean depth maps, closeups, and distant landscapes,            |
# |                   | but can look artificial if depth isn't smooth.                                |
# │ >100              │ Often too much — creates ghosting, stretching, or reveals depth map errors.   |
# └───────────────────┴───────────────────────────────────────────────────────────────────────────────┘

# Supports both parallel viewing (left eye sees left image) and cross-eyed viewing (left eye sees right image).

# For videos, includes temporal smoothing to help reduce flickering/jittering between frames.
# ┌────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
# │ Temporal Smoothing │                               Effect                                                                    │
# │ Value              |                                                                                                         |
# ├────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────┤
# │ 0.0                │  No smoothing — each frame is handled independently.                                                    │
# │ 0.1 – 0.3          │  Mild-moderate smoothing — balances temporal consistency without too much lag.                          │
# │ 0.5                │  Strong smoothing — maximizes stability, but may cause lag in depth response during rapid scene changes.│
# └────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────┘


# The convergence parameter controls the zero-disparity plane (where objects appear at screen depth).
# More important if you plan on converting the outputs into anaglyph format
# 
# ┌─────────────────┬─────────────────────────────────────────────────────────────────────────────────┐
# │ Convergence     │                                Effect                                           │
# │ Value           │                                                                                 │
# ├─────────────────┼─────────────────────────────────────────────────────────────────────────────────┤
# │ 0.0             │ Far objects (black in depth map) appear at screen depth                        │
# │ 0.5 (default)   │ Middle depth objects appear at screen depth - balanced for most content        │
# │ 1.0             │ Near objects (white in depth map) appear at screen depth                       │
# └─────────────────┴─────────────────────────────────────────────────────────────────────────────────┘
#
# Proper convergence eliminates excessive "split" and reduces eye strain.
# This applies to anaglyph more than standard SBS
# Adjust convergence based on your content - portraits may benefit from higher values (0.6-0.8),
# while landscapes may work better with lower values (0.3-0.5).

DEBUG_MODE = False  # Set to False to disable debug left/right tinting

# Global target dtype (change this to float32 or float16 as needed)
target_dtype = torch.float16

# global grid caches 
_GRID_CACHE_GS = {}
_GRID_CACHE_MW = {}

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ========================================================================================
# IMAGE
# ========================================================================================
class Y7_SideBySide:
    
    def __init__(self):
        pass

    # ============================================================
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "base_image": ("IMAGE", {
                    "tooltip": "The main image to convert to a side-by-side 3D image"
                }),
                "depth_map": ("IMAGE", {
                    "tooltip": "Grayscale depth map where brighter areas appear closer and darker areas further away"
                }),
                "method": (["mesh_warping", "grid_sampling"], {
                    "default": "mesh_warping",
                    "tooltip": "Select the 3D rendering method:\n- mesh_warping: produces smoother, more natural depth with curved distortion\n- grid_sampling: faster, simpler pixel shifting for a classic stereo effect"
                }),
                "depth_scale": ("INT", {
                    "default": 40,
                    "tooltip": "Controls the strength of the 3D effect - higher values create more pronounced depth"
                }),
                "mode": (["parallel", "cross-eyed"], {
                    "tooltip": "Parallel: For normal, parallel viewing (left eye sees left image)\n-Cross-eyed: For cross-eyed viewing (left eye sees right image)"
                }),
                "depth_blur_strength": ("INT", {
                    "default": 7, 
                    "min": 3, 
                    "max": 33, 
                    "step": 2,
                    "tooltip": "Controls how much to blur the depth map transitions. Higher values create smoother depth transitions but may lose detail. 3-15. Odd values only."
                }),
                "convergence": ("FLOAT", {
                    "default": 0.5,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "tooltip": "Sets the convergence point (zero disparity plane). 0.0 = far objects at screen depth, 0.5 = middle depth at screen, 1.0 = near objects at screen depth. For Anaglyph format."
                }),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "process_image_sbs"
    CATEGORY = "Y7 SBS"


    # ============================================================
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # indicates that the node's state has changed since the last execution, 
        # which signals to ComfyUI that the node needs to be re-executed rather than using cached results.
        return True        

    # ============================================================
    def process_image_sbs(self, method, base_image, depth_map, depth_scale, mode="parallel", depth_blur_strength=7, convergence=0.5):
        
        # add simple 2-step progress bar
        progress = ProgressBar(2)  # Just two steps for individual image        

        progress.update(1)  # First step

        result = None 
        if method == "grid_sampling":
            result = process_image_sbs_grid_sampling(device, base_image, depth_map, depth_scale, mode, depth_blur_strength, convergence)
        elif method == "mesh_warping":            
            result = process_image_sbs_mesh_warping(device, base_image, depth_map, depth_scale, mode, depth_blur_strength, convergence)

        progress.update(1)  # Final step 
        return result # Return the stored result



# ========================================================================================
# VIDEO
# ========================================================================================
class Y7_VideoSideBySide:
    
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.previous_disparities = None

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "frames": ("IMAGE", {
                    "tooltip": "Sequence of video frames to convert to side-by-side 3D"
                }),
                "depth_maps": ("IMAGE", {
                    "tooltip": "Sequence of depth maps corresponding to each frame"
                }),
                "method": (["mesh_warping", "grid_sampling"], {
                    "default": "mesh_warping",
                    "tooltip": "Select the 3D rendering method:\n- mesh_warping: produces smoother, more natural depth with curved distortion\n- grid_sampling: faster, simpler pixel shifting for a classic stereo effect"
                }),
                "depth_scale": ("INT", {
                    "default": 30,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "tooltip": "Controls the strength of the 3D effect - higher values create more pronounced depth"
                }),
                "mode": (["parallel", "cross-eyed"], {
                    "tooltip": "Parallel: For parallel viewing (left eye sees left image). Cross-eyed: For cross-eyed viewing (left eye sees right image)"
                }),
                "depth_blur_strength": ("INT", {
                    "default": 7, 
                    "min": 3, 
                    "max": 33, 
                    "step": 2,
                    "tooltip": "Controls how much to blur the depth map transitions. Higher values create smoother depth transitions but may lose detail. 3-15. Odd values only."
                }),
                "temporal_smoothing": ("FLOAT", {
                    "default": 0.2,
                    "min": 0.0,
                    "max": 0.5,
                    "step": 0.05,
                    "tooltip": "Smoothing between frames (0=none, higher values=more smoothing)"
                }),
                "batch_size": ("INT", {
                    "default": 32,
                    "min": 1,
                    "max": 256,
                    "step": 1,
                    "tooltip": "Number of frames to process at once. Lower values use less memory but may be slower."
                }),
                "convergence": ("FLOAT", {
                    "default": 0.5,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "tooltip": "Sets the convergence point (zero disparity plane). 0.0 = far objects at screen depth, 0.5 = middle depth at screen, 1.0 = near objects at screen depth. For Anaglyph format."
                }),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "process_video_sbs" 
    CATEGORY = "Y7 SBS"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return True
            
    def process_video_sbs(self, frames, depth_maps, method="mesh_warping", depth_scale=30, mode="parallel", 
                        depth_blur_strength=7, temporal_smoothing=0.2, batch_size=16, convergence=0.5):
        """        
        Main function for processing video frames, balancing GPU and CPU memory usage.
        
        Args:
        - frames: Tensor representing sequence of frames (B, H, W, C)
        - depth_maps: Tensor representing sequence of depth maps (B, H, W, 1 or 3)
        - method: "grid_sampling" or "mesh_warping"
        - depth_scale: Integer for pixel shift scaling
        - mode: "parallel" or "cross-eyed"
        - depth_blur_strength: Controls blur kernel size (odd values from 3-15)
        - temporal_smoothing: Float (0.0-0.5) controlling smoothing between frames
        - batch_size: Number of frames to process at once
        
        Returns:
        - processed_frames: Tensor of processed SBS frames (B, H, W*2, C)
        """
        # Get dimensions
        num_frames, height, width, channels = frames.shape
                
        print(f"Processing {num_frames} frames with batch size {batch_size}", color.BLUE)

        # Initialize progress bar
        progress = ProgressBar(num_frames)
        
        # Reset state at the beginning of a new video
        self.previous_disparities = None

        # Get numpy dtype equivalent of target_dtype
        numpy_dtype = {torch.float16: np.float16, torch.float32: np.float32}.get(target_dtype, np.float32)

        # Create a temporary file for memory mapping
        temp_dir = tempfile.gettempdir()
        pid = os.getpid()
        temp_filename = os.path.join(temp_dir, f"comfyui_sbs_memmap_{pid}_{id(self)}.npy")
        print(f"Using temporary memmap file: {temp_filename}", color.YELLOW)

        # Calculate final shape and create the memory-mapped file
        final_shape = (num_frames, height, width * 2, channels)
        try:
            os.makedirs(os.path.dirname(temp_filename), exist_ok=True)
            memmap_array = np.memmap(temp_filename, dtype=numpy_dtype, mode='w+', shape=final_shape)
        except Exception as e:
            print(f"Error creating memory-mapped file: {e}", color.RED)
            raise IOError(f"Failed to create memory-mapped file at {temp_filename}: {e}")

        # Choose processor function once
        if method == "grid_sampling":
            processor_fn = process_image_sbs_grid_sampling
        elif method == "mesh_warping":
            processor_fn = process_image_sbs_mesh_warping
        else:
            raise ValueError(f"Unknown processing method: {method}")
                
        # Pre-allocate reusable tensors for depth processing
        blur_buffer = None
        
        # Process frames in batches
        for i in range(0, num_frames, batch_size):
            # Get current batch indices and size
            end_idx = min(i + batch_size, num_frames)
            current_batch_size = end_idx - i
            
            # Log batch processing status
            print(f"Processing batch {i//batch_size + 1}/{(num_frames + batch_size - 1)//batch_size}: frames {i+1}-{end_idx} of {num_frames}", color.BLUE)
            
            # Move data to GPU with correct dtype and non-blocking transfer
            batch_frames = frames[i:end_idx].to(self.device, dtype=target_dtype, non_blocking=True)
            batch_depth_maps = depth_maps[i:end_idx].to(self.device, dtype=target_dtype, non_blocking=True)
                        
            # Allocate output tensor for this batch
            batch_output = torch.zeros((current_batch_size, height, width*2, channels), 
                                    dtype=target_dtype, device=self.device)
            
            # Process each frame in the batch
            # Reduce memory usage with no_grad context
            with torch.no_grad():  

                for j in range(current_batch_size):
                    # Get current frame and depth map
                    current_frame = batch_frames[j:j+1]  # Keep batch dimension
                    current_depth = batch_depth_maps[j:j+1]
                    
                    # Process depth map efficiently
                    # Ensure depth map format is correct (reuse previous tensor if possible)
                    if current_depth.shape[-1] in (1, 3):
                        # Convert [B, H, W, C] -> [B, C, H, W]
                        depth_permuted = current_depth.permute(0, 3, 1, 2)
                        # If multi-channel, use only first channel
                        if depth_permuted.shape[1] > 1:
                            depth_permuted = depth_permuted[:, 0:1]
                    else:
                        # Already in [B, C, H, W] format or needs unsqueeze
                        depth_permuted = current_depth.unsqueeze(1) if current_depth.ndim == 3 else current_depth
                    
                    # Ensure depth map size matches frame size
                    if depth_permuted.shape[2:] != (height, width):
                        depth_permuted = F.interpolate(depth_permuted, size=(height, width), 
                                                    mode='bilinear', align_corners=False)
                    
                    # Create blur buffer if needed
                    if blur_buffer is None or blur_buffer.shape != depth_permuted.shape:
                        blur_buffer = torch.zeros_like(depth_permuted)
                    
                    # Apply blur efficiently - ensure blur strength is odd value
                    if depth_blur_strength % 2 == 0:
                        depth_blur_strength += 1
                    
                    # apply depth blur to the depth map
                    depth_blurred = apply_depth_blur(depth_permuted, depth_blur_strength)
                                        
                    
                    # Handle temporal smoothing
                    if temporal_smoothing > 0 and self.previous_disparities is not None:
                        # Calculate current disparity
                        current_disparity = depth_blurred * 255.0 * (depth_scale / width)
                        
                        # Blend with previous disparities using lerp (more efficient)
                        # lerp: result = start + alpha * (end - start)
                        blended_disparity = torch.lerp(
                            current_disparity, 
                            self.previous_disparities.to(current_disparity.dtype),
                            temporal_smoothing
                        )
                        
                        # Store for next frame (using clone to avoid reference issues)
                        self.previous_disparities = blended_disparity.clone()
                        
                        # Convert back to depth map
                        depth_for_processing = blended_disparity / (255.0 * (depth_scale / width))
                        
                    else:
                        # No temporal smoothing or first frame
                        depth_for_processing = depth_blurred
                        
                        # Initialize previous disparities if this is first frame
                        if temporal_smoothing > 0 and self.previous_disparities is None:
                            self.previous_disparities = (depth_blurred * 255.0 * (depth_scale / width)).clone()
                    
                    # Convert depth back to [B, H, W, C] format for processor
                    depth_for_frame = depth_for_processing.permute(0, 2, 3, 1)
                    
                    # Process frame with processor function
                    processed_frame = processor_fn(
                        self.device, current_frame, depth_for_frame, 
                        depth_scale, mode, depth_blur_strength, convergence
                    )[0]
                    
                    # Store in output tensor (direct assignment, no copies)
                    batch_output[j] = processed_frame[0]
            
            # Move processed batch to CPU and store in memmap
            try:
                # Efficient CPU transfer and numpy conversion
                numpy_batch = batch_output.cpu().numpy()
                
                # Write directly to memmap
                memmap_array[i:end_idx] = numpy_batch.astype(numpy_dtype, copy=False)
                
            except Exception as e:
                print(f"Error writing batch to memmap: {e}", color.RED)
                # Clean up
                del memmap_array
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
                raise IOError(f"Failed to write to memory-mapped file: {e}")
            
            # Update progress
            progress.update(current_batch_size)
            
            # Free GPU memory
            del batch_frames
            del batch_depth_maps
            del batch_output
            torch.cuda.empty_cache()

        # Reset state after processing
        self.previous_disparities = None

        # Flush memmap changes to disk
        memmap_array.flush()

        # Create final tensor from memmap
        try:
            print(f"Creating final tensor from memmap", color.YELLOW)
            final_tensor = torch.from_numpy(memmap_array).to(target_dtype)
            print(f"Successfully created final tensor", color.GREEN)
            return (final_tensor,)
        except Exception as e:
            print(f"Error creating final tensor: {e}", color.RED)
            del memmap_array
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            raise RuntimeError(f"Failed to create final tensor: {e}")
        finally:
            # Always clean up resources
            if 'memmap_array' in locals():
                del memmap_array
                gc.collect()
                time.sleep(0.1)  # 100ms delay
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                    print(f"Temporary file deleted", color.GREEN)
                except Exception as e:
                    print(f"Warning: Failed to delete temporary file: {e}", color.ORANGE)

    def process_video_sbs_single_batch(self, frames, depth_maps, method, depth_scale, mode="parallel", depth_blur_strength=7, temporal_smoothing=0.2):
        """
        Internal function that processes a single batch of frames using selected method to create SBS Video frames
        
        Parameters:
        - frames: Tensor representing sequence of frames (B, H, W, C)
        - depth_maps: Tensor representing sequence of depth maps (B, H, W, 1 or 3)
        - depth_scale: Integer for pixel shift scaling
        - mode: "parallel" or "cross-eyed"
        - depth_blur_strength: Controls blur kernel size (odd values from 3-15)
        - temporal_smoothing: Float (0.0-0.5) controlling smoothing between frames
        
        Returns:
        - processed_frames: Tensor of processed SBS frames (B, H, W*2, C)
        """

        # print(f"Processing Grid Sampling Method", color.BLUE)

        # Move tensors to device, 
        frames = frames.to(self.device, dtype=target_dtype)
        depth_maps = depth_maps.to(self.device, dtype=target_dtype)
        
        # =====================================================================
        # Choose which image processor to use
        if method == "grid_sampling":
            image_processor = process_image_sbs_grid_sampling
        elif method == "mesh_warping":
            image_processor = process_image_sbs_mesh_warping
        else:
            raise ValueError(f"Unknown method: {method}")
                
        # =====================================================================
        # Get dimensions
        num_frames, height, width, channels = frames.shape
        
        # Initialize output tensor for processed frames (on GPU, but only for the current batch size)
        processed_frames = torch.zeros((num_frames, height, width*2, channels), dtype=target_dtype, device=self.device)
        
        # Process each frame with temporal consistency
        with torch.no_grad():
            for i in range(num_frames):
                current_dtype = frames.dtype # Use the potentially downcasted dtype for processing
                # Extract current frame and depth map
                current_frame = frames[i:i+1]  # Keep batch dimension as [1, H, W, C]
                current_depth = depth_maps[i:i+1]  # Keep batch dimension as [1, H, W, C]
                
                # Process depth map using the same approach as in process_image_sbs_grid_sampling
                _, H, W, _ = current_frame.shape

                # Ensure depth_map has shape [B, 1, H, W]
                current_depth_permuted = ensure_depth_map_shape(current_depth, self.device)


                # Ensure depth map matches frame resolution (h, w)
                if current_depth_permuted.shape[2:] != (H, W):
                    # dh, dw = current_depth_permuted.shape[2:]
                    # print(f"Depth map dimension does not match frame dimension. Rescaling depth map.", color.ORANGE)
                    current_depth_permuted = F.interpolate(current_depth_permuted, size=(H, W), mode='bilinear', align_corners=False)
                # else:
                    # print(f"Depth map dimension = frame dimension.", color.GREEN)
                
                # Convert back to NHWC format for compatibility with the rest of the function
                current_depth = current_depth_permuted.permute(0, 2, 3, 1)
                                
                # Apply temporal smoothing to depth maps if enabled
                if temporal_smoothing > 0 and self.previous_disparities is not None:
                    # Convert depth map to disparity (pixel shift)
                    current_depth_permuted = current_depth.permute(0, 3, 1, 2)
                    if current_depth_permuted.shape[1] > 1:
                        current_depth_permuted = current_depth_permuted[:, 0:1, :, :]
                    
                    # Apply blur to current depth map 
                    # ensure is odd value
                    if depth_blur_strength % 2 == 0:
                        depth_blur_strength += 1
                    
                    # apply depth blur to the depth map
                    current_depth_blurred = apply_depth_blur(current_depth_permuted, depth_blur_strength)
                    
                    # Calculate current disparity
                    current_disparity = current_depth_blurred * 255.0 * (depth_scale / width)
                                        
                    # Blend with previous disparities using lerp (more efficient)
                    # lerp: result = start + alpha * (end - start)
                    blended_disparity = torch.lerp(
                        current_disparity, 
                        self.previous_disparities.to(current_disparity.dtype),
                        temporal_smoothing
                    )
                    
                    # Store for next frame (ensure correct dtype)
                    self.previous_disparities = blended_disparity.to(current_dtype)
                    
                    # Convert back to depth map format
                    blended_depth = blended_disparity / (255.0 * (depth_scale / width))
                    blended_depth = blended_depth.permute(0, 2, 3, 1).to(current_dtype) # Ensure correct dtype

                    # Process frame with blended depth
                    # Call the global function, passing the video processor's device
                    processed_frame = image_processor(self.device, current_frame, blended_depth, depth_scale, mode, depth_blur_strength)[0]

                else:
                    # Process frame normally for first frame or if temporal smoothing disabled
                    # Call the global function, passing the video processor's device
                    processed_frame = image_processor(self.device, current_frame, current_depth, depth_scale, mode, depth_blur_strength)[0]
                    
                    # Initialize previous disparities for next frame if this is first frame
                    if temporal_smoothing > 0 and self.previous_disparities is None:
                        # Convert depth map to disparity for next frame
                        depth_permuted = current_depth.permute(0, 3, 1, 2)
                        if depth_permuted.shape[1] > 1:
                            depth_permuted = depth_permuted[:, 0:1, :, :]
                        
                        # Apply same blur as in processing
                        if depth_blur_strength % 2 == 0:
                            depth_blur_strength += 1
                        
                        # apply depth blur to the depth map                        
                        depth_blurred = apply_depth_blur(depth_permuted, depth_blur_strength)                        

                        
                        # Store disparity for next frame (ensure correct dtype)
                        self.previous_disparities = (depth_blurred * 255.0 * (depth_scale / width)).to(current_dtype)
                
                # Store processed frame in output tensor
                processed_frames[i] = processed_frame[0].to(target_dtype)  # Remove batch dimension and ensure target dtype
        
        # Note: We do NOT reset self.previous_disparities here anymore
        # This allows temporal smoothing to continue across batch boundaries
        # The reset is now handled in the main process_video_sbs method
        
        return (processed_frames,)




# ======================= Processing Functions =======================
# IMAGE - GRID SAMPLING
def process_image_sbs_grid_sampling(device, base_image, depth_map, depth_scale, mode="parallel", depth_blur_strength=7, convergence=0.5):
    """
    Create a side-by-side (SBS) stereoscopic image using grid_sampling method
    This implementation uses efficient vectorized operations for better performance.       
    
    Args:
    - device: The torch device (e.g., 'cuda' or 'cpu').
    - base_image: Tensor representing the base image (B, H, W, C).
    - depth_map: Tensor representing the depth map (B, H, W, 1 or 3).
    - depth_scale: Integer for pixel shift scaling.
    - mode: "parallel" or "cross-eyed".
    - depth_blur_strength: controls blur kernel size (odd values from 3-15). 
      - Make edges/transitions between depths smoother (at cost of a little detail loss).
    
    Returns:
    - sbs_image_tensor: (B, H, W*2, C)
    """

    # print(f"Processing Grid Sampling Method", color.BLUE)

    # Move tensors to device with target_dtype
    base_image = base_image.to(device, dtype=target_dtype)
    depth_map = depth_map.to(device, dtype=target_dtype)

    # Get dimensions and original dtype
    b, h, w, c = base_image.shape
    # original_dtype = base_image.dtype
    
    # Reorder and convert the base_image from [B, H, W, C] to [B, C, H, W] for PyTorch operations
    image = base_image.permute(0, 3, 1, 2)

    # ==============================================================
    # Depth map                
    # Ensure depth_map has the shape [B, 1, H, W]
    depth_map = ensure_depth_map_shape(depth_map, device)

    # Ensure depth map matches base image's resolution. (height, width)
    if depth_map.shape[2:] != (h, w):
        # dh, dw = depth_map.shape[2:]
        # resize depth map to match the base_image's w and h
        # print(f"Depth map dimension does not match base image dimension. Rescaling depth map.", color.ORANGE)
        depth_map = F.interpolate(depth_map, size=(h, w), mode='bilinear', align_corners=False)
    # else:
        # print(f"Depth map dimension = base_image dimension.", color.GREEN)

    
    # Apply Gaussian blur to smooth depth transitions
    # Ensure depth_blur_strength is odd
    if depth_blur_strength % 2 == 0:
        depth_blur_strength += 1
    
    
    # apply depth blur to the depth map
    depth_map = apply_depth_blur(depth_map, depth_blur_strength)
    
    # Apply convergence adjustment to depth map
    # Convergence sets the zero-disparity plane (objects that appear at screen depth)
    # 0.0 = far objects at screen depth, 1.0 = near objects at screen depth
    adjusted_depth = depth_map - convergence
    
    # Calculate disparity (pixel shift)
    # disparity = adjusted_depth * (depth_scale / w)
    disparity = adjusted_depth * 255.0 * (depth_scale / w)

    # Get cached coordinate grid
    y_grid, x_grid = get_grid_gs(h, w, target_dtype, device)
    
    # Expand grid to batch size if needed
    if b > 1:
        x_grid = x_grid.expand(b, 1, h, w)
    
    # Compute left and right shifts
    shift_left = x_grid - disparity
    shift_right = x_grid + disparity
    
    # Normalize coordinates for grid_sample (-1 to 1)
    def normalize_coords(x_shifted):
        return 2.0 * x_shifted / (w - 1) - 1.0
    
    x_left = normalize_coords(shift_left)
    x_right = normalize_coords(shift_right)
    
    # Normalize y coordinates (use cached grid)
    y_norm = 2.0 * y_grid.to(target_dtype) / (h - 1) - 1.0
    
    # Expand y grid to batch size if needed
    if b > 1:
        y_norm = y_norm.expand(b, 1, h, w)
    
    # Create sampling grids
    grid_left = torch.stack((x_left.squeeze(1), y_norm.squeeze(1)), dim=-1)
    grid_right = torch.stack((x_right.squeeze(1), y_norm.squeeze(1)), dim=-1)
    
    # Convert to float32 for grid_sample operation
    image_float = image.to(torch.float32)
    grid_left_float = grid_left.to(torch.float32)
    grid_right_float = grid_right.to(torch.float32)
    
    # Sample pixels with float32 precision
    left_view = F.grid_sample(image_float, grid_left_float, mode='bilinear', padding_mode='border', align_corners=True)
    right_view = F.grid_sample(image_float, grid_right_float, mode='bilinear', padding_mode='border', align_corners=True)
    
    # Convert back to target_dtype
    left_view = left_view.to(target_dtype)
    right_view = right_view.to(target_dtype)
    
    # ==== DEBUG LEFT/RIGHT WITH TINTING ====
    if DEBUG_MODE:
        # Helps visually confirm which image is for which eye
        left_view = left_view * torch.tensor([1.0, 0.5, 0.5], device=left_view.device).view(1, 3, 1, 1)   # reddish
        right_view = right_view * torch.tensor([0.5, 1.0, 1.0], device=right_view.device).view(1, 3, 1, 1)  # cyan
    # =======================================

    # Handle viewing mode
    if mode == "parallel":
        # For parallel viewing, left eye image goes on left side
        sbs = torch.cat([left_view, right_view], dim=3)
    elif mode == "cross-eyed":
        # For cross-eyed viewing, right eye image goes on left side
        sbs = torch.cat([right_view, left_view], dim=3)
    
    # Convert back to [B, H, W*2, C] format for ComfyUI
    sbs_image_tensor = sbs.permute(0, 2, 3, 1)
    
    return (sbs_image_tensor,)


# IMAGE - MSH WARPING
def process_image_sbs_mesh_warping(device, base_image, depth_map, depth_scale=30, mode="parallel", depth_blur_strength=7, convergence=0.5):
    """
    Creates a side-by-side stereoscopic image using simple mesh warping method
    
    Args:
    - device: The torch device (e.g., 'cuda' or 'cpu').
    - base_image: torch.Tensor of shape [B, H, W, C], values in [0, 1]
    - depth_map: torch.Tensor of shape [B, H, W, 1] or [B, H, W, C], values in [0, 1]
    - depth_scale: Controls the strength of the 3D effect (used to calculate eye_separation)
    - mode: "parallel" or "cross-eyed" viewing mode
    - depth_blur_strength: Controls blur kernel size (odd values from 3-15).
    
    Returns:
    - Tuple containing SBS image tensor of shape [B, H, W*2, C]
    """

    # print(f"Processing Mesh Warping Method", color.BLUE)

    # Move tensors to device and ensure they're using target_dtype
    base_image = base_image.to(device, dtype=target_dtype)
    depth_map = depth_map.to(device, dtype=target_dtype)

    # # Move tensors to device
    # base_image = base_image.to(device)
    # depth_map = depth_map.to(device)

    B, H, W, C = base_image.shape
    
    # Calculate eye separation based on image width and depth scale
    # Using a more direct relationship with image width
    eye_separation = depth_scale / (W * 2)  # More intuitive scaling
    
    # ==============================================================
    # Depth map
    # Ensure depth_map has the shape [B, 1, H, W]
    depth_map = ensure_depth_map_shape(depth_map, device)

    # Ensure depth map matches base image resolution
    if depth_map.shape[2:] != (H, W):
        dh, dw = depth_map.shape[2:]
        # print(f"Depth map dimension = {dw}x{dh}", color.ORANGE)
        # print(f"Base image dimension = {H}x{W}", color.ORANGE)

        # resize depth map to match the base_image's w and h
        # print(f"Depth map dimension does not match base image dimension. Rescaling depth map.", color.ORANGE)
        depth_map = F.interpolate(depth_map, size=(H, W), mode='bilinear', align_corners=False)
    # else:
        # print(f"Depth map dimension = base_image dimension.", color.GREEN)

    # =============================================================
    # Apply Gaussian blur to smooth depth transitions
    # make sure depth_blur_strength is odd value
    if depth_blur_strength % 2 == 0:
        depth_blur_strength += 1
    
    
    # apply depth blur to the depth map
    depth_map = apply_depth_blur(depth_map, depth_blur_strength)

    # =============================================================
    # Apply convergence adjustment to depth map
    # Convergence sets the zero-disparity plane (objects that appear at screen depth)
    # 0.0 = far objects at screen depth, 1.0 = near objects at screen depth
    adjusted_depth = depth_map - convergence
    
    # Convert adjusted depth map to disparity.
    # Assumes depth map is normalized: white (1.0) = near, black (0.0) = far.
    # Using adjusted depth directly as disparity makes nearby objects shift more,
    # creating a natural stereoscopic effect.
    disparity = adjusted_depth

    base_grid = get_cached_grid_mw(H, W, target_dtype, device)
    grid = base_grid.unsqueeze(0).expand(B, H, W, 2)

    # Compute offset for each eye
    left_grid = grid.clone()
    right_grid = grid.clone()

    # Apply disparity to all batch items, not just the first one
    for b in range(B):
        left_grid[b, ..., 0] -= eye_separation * disparity[b, 0]
        right_grid[b, ..., 0] += eye_separation * disparity[b, 0]

    # Convert to float32 for F.grid_sample operation (F.grid_sample expects floast32)
    # Permute base image for processing
    base_image_nchw = base_image.permute(0, 3, 1, 2)
    
    # Handle grid_sample with the right types
    # Always convert both image and grid to float32 for grid_sample operation
    base_image_nchw_float = base_image_nchw.to(torch.float32)
    left_grid_float = left_grid.to(torch.float32)
    right_grid_float = right_grid.to(torch.float32)
    
    # Perform grid sampling with float32
    left = F.grid_sample(base_image_nchw_float, left_grid_float, mode='bilinear', padding_mode='border', align_corners=True)
    right = F.grid_sample(base_image_nchw_float, right_grid_float, mode='bilinear', padding_mode='border', align_corners=True)
    
    # Convert back to target_dtype 
    left = left.to(target_dtype)
    right = right.to(target_dtype)

    # Convert to NHWC
    left = left.permute(0, 2, 3, 1)
    right = right.permute(0, 2, 3, 1)

    # ==== DEBUG LEFT/RIGHT WITH TINTING ====
    if DEBUG_MODE:
        
        # Help visually confirm which image is for which eye: left = red tint, right = cyan tint

        # Apply a reddish tint to left eye image 
        left = left * torch.tensor([1.0, 0.5, 0.5], device=left.device)

        # Apply a cyan tint to right eye image (no red, green/blue remain)
        right = right * torch.tensor([0.5, 1.0, 1.0], device=right.device)

    # =======================================

    # Concatenate side by side with mode handling
    if mode == "parallel":
        sbs_image_tensor = torch.cat([left, right], dim=2)  # left image on the left
    else:  # Cross-eyed
        sbs_image_tensor = torch.cat([right, left], dim=2)  # left image on the right

    # Return as a tuple to match the expected return format
    return (sbs_image_tensor,)



# ========================= Helper Functions =========================

# ===========================================
def ensure_depth_map_shape(depth_map, device):
    """
    Ensures depth map image tensor has the shape [B, 1, H, W] required for processing.
    
    Parameters:
    - depth_map: Input depth map tensor in various possible formats
    - device: Device to move the tensor to (cuda, cpu etc)
    
    Returns:
    - Depth map tensor with shape [B, 1, H, W]
    """
    # depth map is 4D tensor and the last element is 1 (single channel)
    if depth_map.ndim == 4 and depth_map.shape[-1] == 1:
        # reorder and convert
        depth_map = depth_map.permute(0, 3, 1, 2)
    # depth map is a 4D tensor with 3 channels [B, H, W, 3] (even though only one channel is needed)
    elif depth_map.ndim == 4 and depth_map.shape[-1] == 3:
        # Permute [B, H, W, 3] to [B, 3, H, W] then extract only the first channel
        depth_map = depth_map.permute(0, 3, 1, 2)[:, :1, :, :]
    # depth map is a 3D tensor with no channel info [B, H, W] 
    elif depth_map.ndim == 3:
        # add a new dimension at index 1 to represent the channel [B, 1, H, W] 
        depth_map = depth_map.unsqueeze(1)
    
    return depth_map.to(device)

def apply_depth_blur(depth_map, blur_strength):
    """
    Apply guassian (approximated) blur to depth map for smoother depth transitions
    
    Args:
        depth_map: Tensor of shape [B, C, H, W]
        blur_strength: Controls kernel size (odd values from 3-15)
        
    Returns:
        Blurred depth map
    """
    # Ensure blur_strength is odd
    if blur_strength % 2 == 0:
        blur_strength += 1
    
    # Calculate padding
    h_pad = blur_strength // 2
    v_pad = blur_strength // 2
        
    # First horizontal pass
    blurred = F.avg_pool2d(
        depth_map,
        kernel_size=(1, blur_strength),
        stride=1,
        padding=(0, h_pad)
    )
    
    # Vertical pass
    blurred = F.avg_pool2d(
        blurred,
        kernel_size=(blur_strength, 1),
        stride=1,
        padding=(v_pad, 0)
    )
    
    # Second pass for smoother result
    blurred = F.avg_pool2d(
        blurred,
        kernel_size=(1, blur_strength),
        stride=1,
        padding=(0, h_pad)
    )
    
    blurred = F.avg_pool2d(
        blurred,
        kernel_size=(blur_strength, 1),
        stride=1,
        padding=(v_pad, 0)
    )
    
    return blurred

# =======================================
def get_grid_gs(h, w, dtype, device):
    key = (h, w, dtype)
    if key not in _GRID_CACHE_GS:
        y, x = torch.meshgrid(
            torch.arange(h, device=device, dtype=dtype),
            torch.arange(w, device=device, dtype=dtype),
            indexing='ij'
        )
        # keep them separate to save one extra tensor
        _GRID_CACHE_GS[key] = (y.unsqueeze(0).unsqueeze(0),      # shape (1,1,H,W)
                               x.unsqueeze(0).unsqueeze(0))
    return _GRID_CACHE_GS[key]

# =======================================
def get_cached_grid_mw(height, width, dtype, device):
    """Get a cached coordinate grid or create a new one if needed"""
    # Create a key based on the dimensions and dtype
    cache_key = (height, width, dtype)
    
    # Check if we already have this grid cached
    if cache_key not in _GRID_CACHE_MW:
        # Create the coordinate grid
        y, x = torch.meshgrid(
            torch.linspace(-1, 1, height, device=device, dtype=dtype),
            torch.linspace(-1, 1, width, device=device, dtype=dtype),
            indexing='ij'
        )
        grid = torch.stack((x, y), dim=-1)
        
        # Cache it for future use
        _GRID_CACHE_MW[cache_key] = grid
        
    # Return the cached grid
    return _GRID_CACHE_MW[cache_key]

# =======================================
def map_torch_dtype_to_numpy(torch_dtype):
    dtype_map = {
        torch.float16: np.float16,
        torch.float32: np.float32,
        torch.bfloat16: np.float16,  # fallback but safe for most GPU inference
        torch.uint8: np.uint8
    }
    if torch_dtype not in dtype_map:
        print(f"[Warning] Unknown dtype {torch_dtype}, falling back to np.float16", color.ORANGE)
    return dtype_map.get(torch_dtype, np.float16)
