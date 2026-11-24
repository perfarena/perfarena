# Create a hash set and add elements from nums1
seen = set(nums1)

# Initialize the output list
output = []

# Iterate through nums2 to find common elements
for n in nums2:
    if n in seen:
        output.append(n)
        seen.remove(n)  # Remove the element to avoid duplicates

return output