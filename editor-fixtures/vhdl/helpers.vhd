package helpers is
  -- step in this comment is deliberately not a reference.
  function step(value : integer) return integer;
end package;

package body helpers is
  function step(value : integer) return integer is
  begin
    return value + 1;
  end function;
end package body;
