def numJewelsInStones(J, S):
    numJewels = 0
    for stone in S:
        if J.find(stone) != -1:
            numJewels += 1
    return numJewels

# Example usage
J = "aA"
S = "aAAbbbb"
print(numJewelsInStones(J, S))  # Output: 3