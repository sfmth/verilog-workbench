`default_nettype none
`timescale 1ns/1ns

module processing_element #(
    parameter KERNEL_NBITS = 4
    )(
    // image kernal to be convoluted 5x5 kernel
    input wire [KERNEL_NBITS-1:0] kernel_0,
    input wire [KERNEL_NBITS-1:0] kernel_1,
    input wire [KERNEL_NBITS-1:0] kernel_2,
    input wire [KERNEL_NBITS-1:0] kernel_3,
    input wire [KERNEL_NBITS-1:0] kernel_4,
    input wire [KERNEL_NBITS-1:0] kernel_5,
    input wire [KERNEL_NBITS-1:0] kernel_6,
    input wire [KERNEL_NBITS-1:0] kernel_7,
    input wire [KERNEL_NBITS-1:0] kernel_8,
    input wire [KERNEL_NBITS-1:0] kernel_9,
    input wire [KERNEL_NBITS-1:0] kernel_10,
    input wire [KERNEL_NBITS-1:0] kernel_11,
    input wire [KERNEL_NBITS-1:0] kernel_12,
    input wire [KERNEL_NBITS-1:0] kernel_13,
    input wire [KERNEL_NBITS-1:0] kernel_14,
    input wire [KERNEL_NBITS-1:0] kernel_15,
    input wire [KERNEL_NBITS-1:0] kernel_16,
    input wire [KERNEL_NBITS-1:0] kernel_17,
    input wire [KERNEL_NBITS-1:0] kernel_18,
    input wire [KERNEL_NBITS-1:0] kernel_19,
    input wire [KERNEL_NBITS-1:0] kernel_20,
    input wire [KERNEL_NBITS-1:0] kernel_21,
    input wire [KERNEL_NBITS-1:0] kernel_22,
    input wire [KERNEL_NBITS-1:0] kernel_23,
    input wire [KERNEL_NBITS-1:0] kernel_24,

    // incoming image bit
    input wire image_bit,

    // output MAC results
    output wire [6:0] column_mac0,
    output wire [6:0] column_mac1,
    output wire [6:0] column_mac2,
    output wire [6:0] column_mac3,
    output wire [6:0] column_mac4,

    // flip flop control signals
    input wire clk, shift, reset
    );

    // concatenate kernal in one signal
    wire [KERNEL_NBITS-1:0] kernel [24:0];
    assign kernel[0]  = kernel_0;
    assign kernel[1]  = kernel_1;
    assign kernel[2]  = kernel_2;
    assign kernel[3]  = kernel_3;
    assign kernel[4]  = kernel_4;
    assign kernel[5]  = kernel_5;
    assign kernel[6]  = kernel_6;
    assign kernel[7]  = kernel_7;
    assign kernel[8]  = kernel_8;
    assign kernel[9]  = kernel_9;
    assign kernel[10] = kernel_10;
    assign kernel[11] = kernel_11;
    assign kernel[12] = kernel_12;
    assign kernel[13] = kernel_13;
    assign kernel[14] = kernel_14;
    assign kernel[15] = kernel_15;
    assign kernel[16] = kernel_16;
    assign kernel[17] = kernel_17;
    assign kernel[18] = kernel_18;
    assign kernel[19] = kernel_19;
    assign kernel[20] = kernel_20;
    assign kernel[21] = kernel_21;
    assign kernel[22] = kernel_22;
    assign kernel[23] = kernel_23;
    assign kernel[24] = kernel_24;

    // image bit registers (shift register)
    reg [4:0] bit_img;
    always @(posedge clk) begin
        if (reset) begin
            bit_img[0] <= 0;
            bit_img[1] <= 0;
            bit_img[2] <= 0;
            bit_img[3] <= 0;
            bit_img[4] <= 0;
        end else if (shift) begin
            bit_img[4] <= image_bit;
            bit_img[3] <= bit_img[4];
            bit_img[2] <= bit_img[3];
            bit_img[1] <= bit_img[2];
            bit_img[0] <= bit_img[1];
        end
    end


    // multipliers
    wire [KERNEL_NBITS-1:0] mult_out [24:0];
    genvar i;
    generate
        for (i=0;i<25;i=i+1) begin
            assign mult_out[i] = (bit_img[i/5]) ? kernel[i] : 0;
        end
    endgenerate


    // adders
    assign column_mac0 = mult_out[0] +  mult_out[5] +  mult_out[10] + mult_out[15] + mult_out[20];
    assign column_mac1 = mult_out[1] +  mult_out[6] +  mult_out[11] + mult_out[16] + mult_out[21];
    assign column_mac2 = mult_out[2] +  mult_out[7] +  mult_out[12] + mult_out[17] + mult_out[22];
    assign column_mac3 = mult_out[3] +  mult_out[8] +  mult_out[13] + mult_out[18] + mult_out[23];
    assign column_mac4 = mult_out[4] +  mult_out[9] +  mult_out[14] + mult_out[19] + mult_out[24];

    /* // adders */
    /* assign column_mac0 = mult_out[0] +  mult_out[1] +  mult_out[2] +  mult_out[3] +  mult_out[4]; */
    /* assign column_mac1 = mult_out[5] +  mult_out[6] +  mult_out[7] +  mult_out[8] +  mult_out[9]; */
    /* assign column_mac2 = mult_out[10] + mult_out[11] + mult_out[12] + mult_out[13] + mult_out[14]; */
    /* assign column_mac3 = mult_out[15] + mult_out[16] + mult_out[17] + mult_out[18] + mult_out[19]; */
    /* assign column_mac4 = mult_out[20] + mult_out[21] + mult_out[22] + mult_out[23] + mult_out[24]; */




endmodule
