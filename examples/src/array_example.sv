`default_nettype none
`timescale 1ns/1ps

module array_example (
    input  logic       clk,
    input  logic       write_enable,
    input  logic [1:0] address,
    input  logic [7:0] write_data,
    output logic [7:0] read_data
);
    logic [7:0] memory [0:3];

    always_ff @(posedge clk) begin
        if (write_enable) begin
            memory[address] <= write_data;
        end
    end

    assign read_data = memory[address];
endmodule
