def numJewelsInStones(jewels, stones):
    jewel_set = set(jewels)
    count = 0
    for s in stones:
        if s in jewel_set:
            count += 1
    return count