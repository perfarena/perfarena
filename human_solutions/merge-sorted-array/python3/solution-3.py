# Declare pointers
i = m + n - 1
p1 = m - 1
p2 = n - 1

# Merge arrays
while p1 >= 0 and p2 >= 0:
    if nums1[p1] > nums2[p2]:
        nums1[i] = nums1[p1]
        p1 -= 1
    else:
        nums1[i] = nums2[p2]
        p2 -= 1
    i -= 1

# Fill in remaining elements from nums2
while p2 >= 0:
    nums1[i] = nums2[p2]
    i -= 1
    p2 -= 1