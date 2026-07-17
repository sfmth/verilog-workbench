library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity vhdl_beginner_adder is
  port (
    left  : in  std_logic_vector(7 downto 0);
    right : in  std_logic_vector(7 downto 0);
    sum   : out std_logic_vector(7 downto 0);
    carry : out std_logic
  );
end entity;

architecture rtl of vhdl_beginner_adder is
begin
  -- process(all) is a VHDL-2008 combinational process.
  calculate_sum : process(all)
    variable full_sum : unsigned(8 downto 0);
  begin
    full_sum := ('0' & unsigned(left)) + ('0' & unsigned(right));
    sum <= std_logic_vector(full_sum(7 downto 0));
    carry <= full_sum(8);
  end process;
end architecture;
