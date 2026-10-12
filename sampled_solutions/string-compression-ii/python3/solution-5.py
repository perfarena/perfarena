def widest_vertical_area(points):
    points.sort()
    Max_width = 0
    for i in range(len(points) - 1):
        width = points[i + 1] - points[i]
        Max_width = max(Max_width, width)
    return Max_width