`default_nettype none
`timescale 1ns/1ns

module shift_add_mult (
    input wire [3:0] beta,
    input wire [7:0] potential_in,
    output reg [7:0] mult_ans
    );

    always @(*) begin
        case (beta)
            4'd0:   mult_ans <= 0;
            4'd1:   mult_ans <= potential_in >> 3;
            4'd2:   mult_ans <= potential_in >> 2;
            4'd3:   mult_ans <= (potential_in >> 2) + (potential_in >> 3);
            4'd4:   mult_ans <= (potential_in >> 1);
            4'd5:   mult_ans <= (potential_in >> 1) + (potential_in >> 3);
            4'd6:   mult_ans <= (potential_in >> 1) + (potential_in >> 2);
            4'd7:   mult_ans <= (potential_in >> 1) + (potential_in >> 2) + (potential_in >> 3);
            4'd8:   mult_ans <= potential_in;
            default:    mult_ans <= 4'bx;
        endcase
    end

endmodule
