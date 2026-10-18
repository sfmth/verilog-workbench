package unrelated is
  function step(value : integer) return integer;
end package;
package body unrelated is
  function step(value : integer) return integer is
  begin
    return value - 1;
  end function;
end package body;

use work.helpers.all;
entity other is
end entity;
architecture rtl of other is
  constant result : integer := step(2);
begin
end architecture;
