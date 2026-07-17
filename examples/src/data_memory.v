`default_nettype none
`timescale 1ns/1ns

module data_memory (
    input wire clk,
    input wire write_enable,
    input wire [31:0] address,
    input wire [31:0] write_data,
    output wire [31:0] read_data
    );

    reg [31:0] memory [0:126];
    wire [29:0] word_address = address[31:2];

    always @(posedge clk) begin
        if (write_enable && word_address < 127)
            memory[word_address] <= write_data;
    end

    assign read_data = (word_address < 127) ? memory[word_address] : 32'b0;
endmodule

`default_nettype wire
