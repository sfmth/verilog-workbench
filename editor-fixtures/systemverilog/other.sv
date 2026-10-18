package unrelated;
  function automatic integer step(input integer value);
    return value - 1;
  endfunction
endpackage
module other;
  integer result;
  initial result = helpers::step(2);
endmodule
