def topKFrequent(nums, k):
    # Step 1: Create a hash map to store element counts
    count_map = {}
    for num in nums:
        count_map[num] = count_map.get(num, 0) + 1

    # Step 2: Convert hash map to bucket
    bucket = [[] for _ in range(len(nums) + 1)]
    for key, value in count_map.items():
        bucket[value].append(key)

    # Step 3: Collect elements from the bucket
    solution = []
    for i in range(len(bucket) - 1, -1, -1):
        if len(bucket[i]) > 0:
            solution.extend(bucket[i])
        if len(solution) >= k:
            break

    return solution[:k]