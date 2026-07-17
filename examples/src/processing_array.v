`default_nettype none
`timescale 1ns/1ns


//`include "/home/farhad/github/spyeyeriss/verilog-workbench/src/processing_element.v"

`define K32_5    3'b000
`define K32_3    3'b001
`define K16_5    3'b010
`define K16_3    3'b011
`define K8_5     3'b100
`define K8_3     3'b101

module processing_array #(
    parameter KERNEL_NBITS = 4
    )(

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

    input wire [31:0] image_row,
    input wire [2:0] kernel_mode,
    output reg [255:0] potential_out,

    input wire clk, shift, reset
    );



    // generate PEs
    wire [6:0] out_column_mac_all [4:0][31:0];
    genvar i;
    generate
        for (i=0;i<32;i=i+1) begin
            processing_element #(.KERNEL_NBITS(KERNEL_NBITS)) pe(
                .kernel_0(kernel_0),
                .kernel_1(kernel_1),
                .kernel_2(kernel_2),
                .kernel_3(kernel_3),
                .kernel_4(kernel_4),
                .kernel_5(kernel_5),
                .kernel_6(kernel_6),
                .kernel_7(kernel_7),
                .kernel_8(kernel_8),
                .kernel_9(kernel_9),
                .kernel_10(kernel_10),
                .kernel_11(kernel_11),
                .kernel_12(kernel_12),
                .kernel_13(kernel_13),
                .kernel_14(kernel_14),
                .kernel_15(kernel_15),
                .kernel_16(kernel_16),
                .kernel_17(kernel_17),
                .kernel_18(kernel_18),
                .kernel_19(kernel_19),
                .kernel_20(kernel_20),
                .kernel_21(kernel_21),
                .kernel_22(kernel_22),
                .kernel_23(kernel_23),
                .kernel_24(kernel_24),
                .image_bit(image_row[i]),
                .column_mac0(out_column_mac_all[0][i]),
                .column_mac1(out_column_mac_all[1][i]),
                .column_mac2(out_column_mac_all[2][i]),
                .column_mac3(out_column_mac_all[3][i]),
                .column_mac4(out_column_mac_all[4][i]),
                .clk(clk),
                .shift(shift),
                .reset(reset)
            );
        end
    endgenerate

    // generate adders
    wire [223:0] potential_out_no_pad;
    genvar j;
    generate
        for (j=0;j<28;j=j+1) begin
            assign potential_out_no_pad[(j*8)+7:j*8] =
                out_column_mac_all[0][j] +
                out_column_mac_all[1][j+1] +
                out_column_mac_all[2][j+2] +
                out_column_mac_all[3][j+3] +
                out_column_mac_all[4][j+4];
        end
    endgenerate

    wire [255:0] potential_out_32b_5x5;
    wire [255:0] potential_out_16b_5x5;
    wire [255:0] potential_out_8b_5x5;

    wire [255:0] potential_out_32b_3x3;
    wire [255:0] potential_out_16b_3x3;
    wire [255:0] potential_out_8b_3x3;

    always @(*) begin
        case (kernel_mode)
            `K32_5:  potential_out = potential_out_32b_5x5;
            `K32_3:  potential_out = potential_out_32b_3x3;
            `K16_5:  potential_out = potential_out_16b_5x5;
            `K16_3:  potential_out = potential_out_16b_3x3;
            `K8_5:   potential_out = potential_out_8b_5x5;
            `K8_3:   potential_out = potential_out_8b_3x3;
            default: potential_out = 256'bx;
        endcase
    end

    wire [7:0] padding [40:0];
    // padding edges of the row
    assign padding[0] =
        out_column_mac_all[2][0] +
        out_column_mac_all[3][1] +
        out_column_mac_all[4][2];

    assign padding[1] =
        out_column_mac_all[1][0] +
        out_column_mac_all[2][1] +
        out_column_mac_all[3][2] +
        out_column_mac_all[4][3];

    assign padding[2] =
        out_column_mac_all[0][28] +
        out_column_mac_all[1][29] +
        out_column_mac_all[2][30] +
        out_column_mac_all[3][31];

    assign padding[3] =
        out_column_mac_all[0][29] +
        out_column_mac_all[1][30] +
        out_column_mac_all[2][31];

    assign padding[4] =
        out_column_mac_all[0][30] +
        out_column_mac_all[1][31];

    // 16b 3x3
    assign padding[5] =
        out_column_mac_all[1][0] +
        out_column_mac_all[2][1];

    assign padding[6] =
        out_column_mac_all[0][14] +
        out_column_mac_all[1][15];

    assign padding[7] =
        out_column_mac_all[1][16] +
        out_column_mac_all[2][17];

    assign padding[8] =
        out_column_mac_all[0][30] +
        out_column_mac_all[1][31];

    // 16b 5x5
    assign padding[9] =
        out_column_mac_all[2][0] +
        out_column_mac_all[3][1] +
        out_column_mac_all[4][2];

    assign padding[12] =
        out_column_mac_all[0][13] +
        out_column_mac_all[1][14] +
        out_column_mac_all[2][15];

    assign padding[13] =
        out_column_mac_all[2][16] +
        out_column_mac_all[3][17] +
        out_column_mac_all[4][18];

    assign padding[15] =
        out_column_mac_all[0][29] +
        out_column_mac_all[1][30] +
        out_column_mac_all[2][31];

    assign padding[10] =
        out_column_mac_all[1][0] +
        out_column_mac_all[2][1] +
        out_column_mac_all[3][2] +
        out_column_mac_all[4][3];

    assign padding[11] =
        out_column_mac_all[0][12] +
        out_column_mac_all[1][13] +
        out_column_mac_all[2][14] +
        out_column_mac_all[3][15];

    assign padding[14] =
        out_column_mac_all[1][16] +
        out_column_mac_all[2][17] +
        out_column_mac_all[3][18] +
        out_column_mac_all[4][19];

    assign padding[16] =
        out_column_mac_all[0][28] +
        out_column_mac_all[1][29] +
        out_column_mac_all[2][30] +
        out_column_mac_all[3][31];

    // 8b 3x3
    assign padding[17] =
        out_column_mac_all[1][0] +
        out_column_mac_all[2][1];

    assign padding[18] =
        out_column_mac_all[0][6] +
        out_column_mac_all[1][7];

    assign padding[19] =
        out_column_mac_all[1][8] +
        out_column_mac_all[2][9];

    assign padding[20] =
        out_column_mac_all[0][14] +
        out_column_mac_all[1][15];

    assign padding[21] =
        out_column_mac_all[1][16] +
        out_column_mac_all[2][17];

    assign padding[22] =
        out_column_mac_all[0][22] +
        out_column_mac_all[1][23];

    assign padding[23] =
        out_column_mac_all[1][24] +
        out_column_mac_all[2][25];

    assign padding[24] =
        out_column_mac_all[0][30] +
        out_column_mac_all[1][31];

    // 8b 5x5
    assign padding[25] =
        out_column_mac_all[2][0] +
        out_column_mac_all[3][1] +
        out_column_mac_all[4][2];

    assign padding[26] =
        out_column_mac_all[1][0] +
        out_column_mac_all[2][1] +
        out_column_mac_all[3][2] +
        out_column_mac_all[4][3];

    assign padding[27] =
        out_column_mac_all[0][4] +
        out_column_mac_all[1][5] +
        out_column_mac_all[2][6] +
        out_column_mac_all[3][7];

    assign padding[28] =
        out_column_mac_all[0][5] +
        out_column_mac_all[1][6] +
        out_column_mac_all[2][7];
    //
    assign padding[29] =
        out_column_mac_all[2][8] +
        out_column_mac_all[3][9] +
        out_column_mac_all[4][10];

    assign padding[30] =
        out_column_mac_all[1][8] +
        out_column_mac_all[2][9] +
        out_column_mac_all[3][10] +
        out_column_mac_all[4][11];

    assign padding[31] =
        out_column_mac_all[0][12] +
        out_column_mac_all[1][13] +
        out_column_mac_all[2][14] +
        out_column_mac_all[3][15];

    assign padding[32] =
        out_column_mac_all[0][13] +
        out_column_mac_all[1][14] +
        out_column_mac_all[2][15];
    //
    assign padding[33] =
        out_column_mac_all[2][16] +
        out_column_mac_all[3][17] +
        out_column_mac_all[4][18];

    assign padding[34] =
        out_column_mac_all[1][16] +
        out_column_mac_all[2][17] +
        out_column_mac_all[3][18] +
        out_column_mac_all[4][19];

    assign padding[35] =
        out_column_mac_all[0][20] +
        out_column_mac_all[1][21] +
        out_column_mac_all[2][22] +
        out_column_mac_all[3][23];

    assign padding[36] =
        out_column_mac_all[0][21] +
        out_column_mac_all[1][22] +
        out_column_mac_all[2][23];
    //
    assign padding[37] =
        out_column_mac_all[2][24] +
        out_column_mac_all[3][25] +
        out_column_mac_all[4][26];

    assign padding[38] =
        out_column_mac_all[1][24] +
        out_column_mac_all[2][25] +
        out_column_mac_all[3][26] +
        out_column_mac_all[4][27];

    assign padding[39] =
        out_column_mac_all[0][28] +
        out_column_mac_all[1][29] +
        out_column_mac_all[2][30] +
        out_column_mac_all[3][31];

    assign padding[40] =
        out_column_mac_all[0][29] +
        out_column_mac_all[1][30] +
        out_column_mac_all[2][31];


    assign potential_out_32b_5x5 = {
        padding[0],
        padding[1],
        potential_out_no_pad,
        padding[2],
        padding[3]
        };

    assign potential_out_32b_3x3 = {
        padding[1],
        potential_out_no_pad,
        padding[2],
        padding[3],
        padding[4]
        };

    assign potential_out_16b_3x3 = {
        padding[5],
        potential_out_no_pad[111:0],
        padding[6],

        padding[7],
        potential_out_no_pad[239:128],
        padding[8]
        };

    assign potential_out_16b_5x5 = {
        padding[9],
        padding[10],
        potential_out_no_pad[95:0],
        padding[11],
        padding[12],

        padding[13],
        padding[14],
        potential_out_no_pad[223:128],
        padding[15],
        padding[16]
        };

    assign potential_out_8b_3x3 = {
        padding[17],
        potential_out_no_pad[47:0],
        padding[18],

        padding[19],
        potential_out_no_pad[111:64],
        padding[20],

        padding[21],
        potential_out_no_pad[175:128],
        padding[22],

        padding[23],
        potential_out_no_pad[239:192],
        padding[24]
        };

    assign potential_out_8b_5x5 = {
        padding[25],
        padding[26],
        potential_out_no_pad[31:0],
        padding[27],
        padding[28],

        padding[29],
        padding[30],
        potential_out_no_pad[95:64],
        padding[31],
        padding[32],

        padding[33],
        padding[34],
        potential_out_no_pad[159:128],
        padding[35],
        padding[36],

        padding[37],
        padding[38],
        potential_out_no_pad[223:192],
        padding[39],
        padding[40]
        };



endmodule

