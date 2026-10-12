# Step 1: Initialize the hash set
numset = set(nums)

# Step 2: Initialize max_length
max_length = 0

# Step 3: Iterate through the numbers in the set
for num in numset:
    # Step 4: Check if num is the start of a sequence
    if num - 1 not in numset:
        # Step 5: Perform a "DFS-like" search
        longest = 1
        while num + longest in numset:
            longest += 1
        # Step 6: Update max_length
        max_length = max(max_length, longest)

# Step 7: Return the max_length
return max_length