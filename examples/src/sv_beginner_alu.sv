module sv_beginner_alu (
  input  logic [7:0] left,
  input  logic [7:0] right,
  input  logic [1:0] operation,
  output logic [7:0] result,
  output logic       carry
);
  logic [8:0] selected_result;

  always_comb begin
    // Give every output a value before choosing the requested operation.
    selected_result = 9'b0;

    case (operation)
      sv_beginner_math_pkg::ALU_ADD:
        selected_result = sv_beginner_math_pkg::add_with_carry(left, right);
      sv_beginner_math_pkg::ALU_AND:
        selected_result[7:0] = left & right;
      sv_beginner_math_pkg::ALU_OR:
        selected_result[7:0] = left | right;
      sv_beginner_math_pkg::ALU_XOR:
        selected_result[7:0] = left ^ right;
      default:
        selected_result = 9'b0;
    endcase
  end

  assign result = selected_result[7:0];
  assign carry = selected_result[8];
endmodule
