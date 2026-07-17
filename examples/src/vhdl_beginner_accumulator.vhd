library ieee;
use ieee.std_logic_1164.all;

entity vhdl_beginner_accumulator is
  port (
    clk    : in  std_logic;
    reset  : in  std_logic;
    enable : in  std_logic;
    addend : in  std_logic_vector(7 downto 0);
    total  : out std_logic_vector(7 downto 0);
    carry  : out std_logic
  );
end entity;

architecture rtl of vhdl_beginner_accumulator is
  signal total_value : std_logic_vector(7 downto 0) := (others => '0');
  signal next_total  : std_logic_vector(7 downto 0);
  signal next_carry  : std_logic;
  signal carry_value : std_logic := '0';
begin
  -- This direct entity instance demonstrates a small VHDL hierarchy.
  add_stage : entity work.vhdl_beginner_adder
    port map (
      left  => total_value,
      right => addend,
      sum   => next_total,
      carry => next_carry
    );

  total <= total_value;
  carry <= carry_value;

  save_total : process(clk)
  begin
    if rising_edge(clk) then
      if reset = '1' then
        total_value <= (others => '0');
        carry_value <= '0';
      elsif enable = '1' then
        total_value <= next_total;
        carry_value <= next_carry;
      else
        carry_value <= '0';
      end if;
    end if;
  end process;
end architecture;
