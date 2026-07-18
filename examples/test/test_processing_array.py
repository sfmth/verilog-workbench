import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles
import random

import math
from cocotb.handle import RealObject
from bitstring import BitArray

from PIL import Image
import numpy as np

np.set_printoptions(threshold=np.inf, linewidth=np.inf)

def text_to_decimal(text):
    ascii_values = [ord(character) for character in text]
    a =int(''.join(str(bin(i)[2:].zfill(8)) for i in ascii_values), 2)
    return a

def sgined_bin_to_int(a):
    a_b = "0b" + str(a)
    return BitArray(bin=a_b).int

def bin_to_int(a):
    a_c = "0b" + str(a)
    return int(a_c,2)

def arr_to_int(a):
    return int("".join(str(i) for i in a.tolist()))

def insert_substring(original_string, substring, index):
    return original_string[:index] + substring + original_string[index:]

def convolve_(image, kernel):
    image = zero_pad_array(image)
    # Get image and kernel dimensions
    image_height, image_width = image.shape
    kernel_height, kernel_width = kernel.shape
    
    # Initialize output image
    output_image = np.zeros_like(image)
    
    # Iterate over each pixel in the output image
    for i in range(image_height - kernel_height + 1):
        for j in range(image_width - kernel_width + 1):
            # Get the current image patch
            patch = image[i:i+kernel_height, j:j+kernel_width]
    
            # Multiply the patch by the kernel and sum the result
            convolved_value = np.sum(patch * kernel)
    
            # Set the output pixel value to the convolved value
            output_image[i+kernel_height//2, j+kernel_width//2] = convolved_value
    
    output_image = remove_padding(output_image)
    return output_image

def zero_pad_array(input_array, pad_width=1):
    return np.pad(input_array, pad_width, mode='constant', constant_values=0)

def remove_padding(input_array, pad_width=1):
    # Ensure pad_width is a tuple
    if isinstance(pad_width, int):
        pad_width = (pad_width,) * input_array.ndim

    # Calculate the slice to remove the padding
    slices = tuple(slice(pw, -pw) for pw in pad_width)

    # Extract the unpadded array using the slice
    return input_array[slices]









clocks_per_phase = 10

async def reset(dut):
    dut.reset.value = 1;
    await ClockCycles(dut.clk, 5)
    dut.reset.value = 0;

@cocotb.test()
async def test_all(dut):
    clock = Clock(dut.clk, 10, "us")

    cocotb.start_soon(clock.start())
    dut.shift.value = 1

    # load kernel
    kernel = np.array([[6,5,9,4,3], [0,3,0,7,1], [6,7,2,2,9], [0,3,0,7,1], [0,3,0,7,1]])
    print()
    print("Kernel:")
    print(np.array(kernel))
    dut.kernel_0.value =  int(kernel[0][0])
    dut.kernel_1.value =  int(kernel[0][1])
    dut.kernel_2.value =  int(kernel[0][2])
    dut.kernel_3.value =  int(kernel[0][3])
    dut.kernel_4.value =  int(kernel[0][4])

    dut.kernel_5.value =  int(kernel[1][0])
    dut.kernel_6.value =  int(kernel[1][1])
    dut.kernel_7.value =  int(kernel[1][2])
    dut.kernel_8.value =  int(kernel[1][3])
    dut.kernel_9.value =  int(kernel[1][4])

    dut.kernel_10.value = int(kernel[2][0])
    dut.kernel_11.value = int(kernel[2][1])
    dut.kernel_12.value = int(kernel[2][2])
    dut.kernel_13.value = int(kernel[2][3])
    dut.kernel_14.value = int(kernel[2][4])

    dut.kernel_15.value = int(kernel[3][0])
    dut.kernel_16.value = int(kernel[3][1])
    dut.kernel_17.value = int(kernel[3][2])
    dut.kernel_18.value = int(kernel[3][3])
    dut.kernel_19.value = int(kernel[3][4])

    dut.kernel_20.value = int(kernel[4][0])
    dut.kernel_21.value = int(kernel[4][1])
    dut.kernel_22.value = int(kernel[4][2])
    dut.kernel_23.value = int(kernel[4][3])
    dut.kernel_24.value = int(kernel[4][4])

    dut.kernel_mode.value = 0



    await reset(dut)
    
    # Load the image
    import os
    image = Image.open(os.path.join(os.path.dirname(__file__), '32-0.png'))
    gray_image = image.convert('L')
    gray_image = np.asarray(gray_image, dtype=float)
    image_range = np.ptp(gray_image)
    if image_range:
        gray_image = (gray_image - np.min(gray_image)) / image_range
    gray_image = np.round(gray_image).astype(int)
    print()
    print("Input Image:")
    print(np.array(gray_image))

    # image = Image.open('/home/farhad/github/spyeyeriss/python-workbench/img/32-0.png')
    # gray_image = image.convert('L')
    # scaler = MinMaxScaler()
    # gray_image = np.round(scaler.fit_transform(gray_image)).astype(int)

    # kernel = np.array([[0,0,1], [0,1,0], [1,0,0]])
    # print("kernel:")
    # print(kernel)
    # same = np.round(signal.fftconvolve(np.array(gray_image), kernel,  mode='same')).astype(int)
    # same_ = convolve_(gray_image, kernel)


    print()
    print("Reference output image (with padding):")
    print(convolve_(gray_image, kernel))
    print()


    # add padding
    zeros_row = np.zeros(32).astype(int)
    # print("input:")
    # print(gray_image)
    image_padded = np.vstack((zeros_row, zeros_row, gray_image, zeros_row, zeros_row))


    out_module = np.empty(32)

    for i in range(image_padded.shape[1]+4): 
        dut.image_row.value = bin_to_int(arr_to_int(np.flip(image_padded[i])))
        await ClockCycles(dut.clk, 1)
        if (i >= 5):
            all_ = bin(dut.potential_out.value)[2:]
            for k in range(256-len(all_)):
                all_ = insert_substring(all_, "0", 0)
            all_list = [all_[i:i+8] for i in range(0, len(all_), 8)]

            row = []
            for value in all_list:
                row.append(bin_to_int(value))
            out_module = np.vstack((out_module, np.flip(row)))
    out_module = np.delete(out_module, 0, axis=0)

    print()
    print("Verilog output image (with padding):")
    print(out_module.astype(int))
    print(out_module.shape)
