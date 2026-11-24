# Code using Array
stones = [7, 8, 2, 4, 1, 2, 1]
while len(stones) > 1:
    stones.sort(reverse=True)
    heaviest, second_heaviest = stones[0], stones[1]
    if heaviest == second_heaviest:
        stones = stones[2:]
    else:
        stones = stones[2:] + [heaviest - second_heaviest]
if stones:
    print(stones[0])
else:
    print(0)