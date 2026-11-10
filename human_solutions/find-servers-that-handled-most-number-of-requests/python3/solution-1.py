count = 0
box_types.sort(key=lambda x: x[1], reverse=True)

for i in range(len(box_types)):
    if box_types[i][0] <= size:
        count += box_types[i][0] * box_types[i][1]
        size -= box_types[i][0]
    else:
        count += size * box_types[i][1]
        size = 0

return count