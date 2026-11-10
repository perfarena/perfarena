def numJewelsInStones(J, S):
    jewel_set = set(J)
    count = 0
    
    for stone in S:
        if stone in jewel_set:
            count += 1
    
    return count

# Example usage
J = "aA"
S = "aAAbbbb"
result = numJewelsInStones(J, S)
print(result)  # Output: 3