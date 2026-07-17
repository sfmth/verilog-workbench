`default_nettype none
`timescale 1ns/1ns

module potential_processor #(
    parameter KERNEL_NBITS = 4
    )(

    //SRAM interface
    output reg [255:0] write_data,
    output reg [4:0] write_address,
    output reg we,

    input wire [255:0] read_data,
    output reg [4:0] read_address,

    input wire [255:0] cnn_in,


    input wire clk, stop, reset
    );




    localparam [3:0] STATE_INIT  = 4'd0;
    localparam [3:0] STATE_START = 4'd1;
    localparam [3:0] STATE_WRITE = 4'd2;
    localparam [3:0] STATE_READ  = 4'd3;

    reg [3:0] state;
    always @(posedge clk) begin
        case (state)
            STATE_INIT: begin
                ;
            end
            STATE_START: begin
                ;
            end
            STATE_WRITE: begin
                write_data <= cnn_in;
                state <= STATE_READ;
            end
            STATE_READ: begin
                ;
            end
            default: state <= STATE_INIT;
        endcase
    end


    /* `ifdef COCOTB_SIM */
    /* initial begin */
    /* $dumpfile ("processing_element.vcd"); */
    /* $dumpvars (0, processing_element); */
    /* #1; */
    /* end */
    /* `endif */

endmodule
