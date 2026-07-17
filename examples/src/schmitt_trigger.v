`default_nettype none
`timescale 1ns/1ns

// there is no need for this module
// yikes!
//
// Turns out we do need it!

`define THRESH_LOW      8'd200
`define THRESH_HIGH     8'd230

module schmitt_trigger (
    input wire [7:0] potential_in,
    output wire spk,
    output wire spkblty_out,
    input wire spkblty_in
    );

    assign spk = spkblty_in & (potential_in > `THRESH_HIGH);
    assign spkblty_out = !spk & (potential_in < `THRESH_LOW);

endmodule
