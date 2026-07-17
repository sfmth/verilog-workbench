library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

architecture rtl of vhdl_beginner_counter is
  signal count_value : unsigned(3 downto 0) := (others => '0');
begin
  count <= std_logic_vector(count_value);

  update_count : process(clk)
  begin
    if rising_edge(clk) then
      -- The _n suffix means reset is active when it is low.
      if reset_n = '0' then
        count_value <= (others => '0');
      elsif enable = '1' then
        count_value <= count_value + 1;
      end if;
    end if;
  end process;
end architecture;
