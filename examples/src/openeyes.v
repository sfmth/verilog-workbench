`default_nettype none
`timescale 1ns/1ns

module openeyes #(
    parameter KERNEL_NBITS = 4
    )(

    //SRAM interface
    output wire [255:0] write_data,
    /* output reg [4:0] write_address, */
    /* output reg we, */

    input wire [255:0] read_data,
    /* output reg [4:0] read_address, */

    input wire [2:0] beta,
    input wire [7:0] uthresh,

    input wire kernel_we,
    input wire [24:0] kernel_write_address,
    input wire [3:0] kernel_write_data,

    input wire [2:0] kernel_mode,

    output wire [31:0] spikes,
    input wire [31:0] image_row,
    input wire shift,

    input wire clk, reset
    );

    wire [255:0] cnn_32n_potential_in, save_32n_potential_out, sram_32n_potential_in;
    /* wire [31:0] spikes; */
    assign cnn_32n_potential_in = potential_out;
    assign write_data = save_32n_potential_out;
    assign sram_32n_potential_in = read_data;
    potential_arithmetic_unit pau0(
        // save
        .cnn_32n_potential_in(cnn_32n_potential_in),
        .save_32n_potential_out(save_32n_potential_out),

        //load
        .sram_32n_potential_in(sram_32n_potential_in),
        .beta(beta),

        .spikes(spikes),
        .uthresh(uthresh)
    );

    wire [255:0] potential_out;
    /* wire [2:0] kernel_mode; */
    /* wire [31:0] image_row; */
    /* wire shift; */

    // kernel storage
    integer i;
    reg [3:0] kernel [24:0];
    always @(posedge clk) begin
        if (reset) begin
            for (i=0; i<25; i=i+1) kernel[i] <= 4'b0000;
        end else if (kernel_we) begin
            kernel[kernel_write_address] <= kernel_write_data;
        end
    end

    processing_array pa0( 
        .kernel_0(kernel[0]),
        .kernel_1(kernel[1]),
        .kernel_2(kernel[2]),
        .kernel_3(kernel[3]),
        .kernel_4(kernel[4]),
        .kernel_5(kernel[5]),
        .kernel_6(kernel[6]),
        .kernel_7(kernel[7]),
        .kernel_8(kernel[8]),
        .kernel_9(kernel[9]),
        .kernel_10(kernel[10]),
        .kernel_11(kernel[11]),
        .kernel_12(kernel[12]),
        .kernel_13(kernel[13]),
        .kernel_14(kernel[14]),
        .kernel_15(kernel[15]),
        .kernel_16(kernel[16]),
        .kernel_17(kernel[17]),
        .kernel_18(kernel[18]),
        .kernel_19(kernel[19]),
        .kernel_20(kernel[20]),
        .kernel_21(kernel[21]),
        .kernel_22(kernel[22]),
        .kernel_23(kernel[23]),
        .kernel_24(kernel[24]),

        .image_row(image_row),
        .kernel_mode(kernel_mode),
        .potential_out(potential_out),

        .clk(clk),
        .shift(shift),
        .reset(reset)
    );



    /* reg [3:0] state; */
    /* always @(posedge clk) begin */
    /*     case (state) */
    /*         `INIT: begin */
    /*             ; */
    /*         end */
    /*         `START: begin */
    /*             ; */
    /*         end */
    /*         `WRITE: begin */
    /*             write_data <= u_out; */
    /*             state <= `READ; */
    /*         end */
    /*         `READ: begin */
    /*             ; */
    /*         end */
    /*         default: state <= `INIT; */
    /*     endcase */
    /* end */


endmodule
