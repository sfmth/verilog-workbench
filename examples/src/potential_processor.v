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




    reg [3:0] state;
    always @(posedge clk) begin
        case (state)
            `INIT: begin
                ;
            end
            `START: begin
                ;
            end
            `WRITE: begin
                write_data <= u_out;
                state <= `READ;
            end
            `READ: begin
                ;
            end
            default: state <= `INIT;
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

