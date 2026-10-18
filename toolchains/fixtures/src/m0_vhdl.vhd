library ieee;
use ieee.std_logic_1164.all;
entity m0_vhdl is
  port(a, b : in std_logic; y : out std_logic);
end entity;
architecture rtl of m0_vhdl is
begin
  y <= a xor b;
end architecture;
