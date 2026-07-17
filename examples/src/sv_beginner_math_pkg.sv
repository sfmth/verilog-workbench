package sv_beginner_math_pkg;
  // Keeping operation numbers in one package avoids repeating magic numbers.
  localparam logic [1:0] ALU_ADD = 2'b00;
  localparam logic [1:0] ALU_AND = 2'b01;
  localparam logic [1:0] ALU_OR  = 2'b10;
  localparam logic [1:0] ALU_XOR = 2'b11;

  function automatic logic [8:0] add_with_carry(
    input logic [7:0] left,
    input logic [7:0] right
  );
    add_with_carry = {1'b0, left} + {1'b0, right};
  endfunction
endpackage
