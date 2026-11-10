# Initialize an empty list to store the intersection elements
result = []

# Use a map to keep track of encountered elements
element_map = {}

# Iterate through nums1 and add elements to the map
for num in nums1:
    if num not in element_map:
        element_map[num] = 1
    else:
        element_map[num] += 1

# Iterate through nums2 to find common elements
for num in nums2:
    if num in element_map and element_map[num] > 0:
        result.append(num)
        element_map[num] = 0

# Convert the result list to an integer array
answer = [num for num in result]

# Return the answer array as the output
return answer