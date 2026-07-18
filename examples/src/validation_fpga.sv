module validation_fpga (
    input  logic clock,
    output logic led = 1'b0
);
    always_ff @(posedge clock)
        led <= ~led;
endmodule
