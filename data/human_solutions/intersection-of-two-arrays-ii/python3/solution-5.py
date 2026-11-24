# Create a hash map for nums1
h_map = {}
for n in nums1:
    h_map[n] = h_map.get(n, 0) + 1

# Find intersection
result = []
for n in nums2:
    if n in h_map and h_map[n] > 0:
        result.append(n)
        h_map[n] -= 1