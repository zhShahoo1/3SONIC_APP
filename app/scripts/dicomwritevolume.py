import os
import numpy as np

def dicom_write_slice(arr, dicom_file, idx, filename, e_r):
    # Flip the array vertically
    arr = np.flip(arr, axis=0)
    arr = np.uint16(256 * arr)
    dicom_file.Rows = arr.shape[0]
    dicom_file.Columns = arr.shape[1]
    
    # Convert the array to bytes and assign it to PixelData
    dicom_file.PixelData = arr.tobytes()
    idx = int(idx)  # Convert idx to an integer
 
    image_position = [0, 0, e_r * idx]
    dicom_file.ImagePositionPatient = image_position

    # Save the DICOM slice
    result = 1
    while result is not None:
        try:
            result = dicom_file.save_as(os.path.join(filename, f'slice{idx}.dcm'))
        except:
            pass
