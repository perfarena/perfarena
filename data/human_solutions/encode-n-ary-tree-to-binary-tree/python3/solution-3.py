def numJewelsInStones(joules: str, stones: str) -> int:
    total = 0
    for stone in stones:
        if stone in joules:
            total += 1
    return total

# Example usage
joules = "aA"
stones = "aAAbbbb"
result = numJewelsInStones(joules, stones)
print(result)  # Output: 3