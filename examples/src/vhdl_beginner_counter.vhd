library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity vhdl_beginner_counter is
  port (
    clk     : in  std_logic;
    reset_n : in  std_logic;
    enable  : in  std_logic;
    count   : out std_logic_vector(3 downto 0)
  );
end entity;
