`default_nettype none
`timescale 1ns/1ps

module test_array_example;
    logic       clk = 0;
    logic       write_enable = 0;
    logic [1:0] address = 0;
    logic [7:0] write_data = 0;
    logic [7:0] read_data;

    array_example dut (
        .clk(clk),
        .write_enable(write_enable),
        .address(address),
        .write_data(write_data),
        .read_data(read_data)
    );

    always #1 clk = ~clk;

    initial begin
        write_enable = 1;
        address = 0;
        write_data = 8'h12;
        #2;
        address = 1;
        write_data = 8'h34;
        #2;
        address = 2;
        write_data = 8'h56;
        #2;
        address = 3;
        write_data = 8'h78;
        #2;
        write_enable = 0;

        address = 0;
        #1;
        if (read_data !== 8'h12) $fatal(1, "memory[0] mismatch");
        address = 1;
        #1;
        if (read_data !== 8'h34) $fatal(1, "memory[1] mismatch");
        address = 2;
        #1;
        if (read_data !== 8'h56) $fatal(1, "memory[2] mismatch");
        address = 3;
        #1;
        if (read_data !== 8'h78) $fatal(1, "memory[3] mismatch");

        $finish;
    end
endmodule
