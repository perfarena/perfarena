# Code using Min Heap
import heapq
stones = [7, 8, 2, 4, 1, 2, 1]
stones = [-stone for stone in stones]
heapq.heapify(stones)
while len(stones) > 1:
    heaviest = heapq.heappop(stones)
    second_heaviest = heapq.heappop(stones)
    if heaviest != second_heaviest:
        heapq.heappush(stones, heaviest - second_heaviest)
if stones:
    print(-stones[0])
else:
    print(0)