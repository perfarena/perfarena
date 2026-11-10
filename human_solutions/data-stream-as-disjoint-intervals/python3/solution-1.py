class SummaryRanges:
    def __init__(self):
        self.intervals = set()

    def addNum(self, val: int) -> None:
        self.intervals.add(val)

    def getIntervals(self) -> List[List[int]]:
        sorted_intervals = sorted(self.intervals)
        result = []
        start = end = None
        for num in sorted_intervals:
            if start is None:
                start = end = num
            elif num == end + 1:
                end = num
            else:
                result.append([start, end])
                start = end = num
        if start is not None:
            result.append([start, end])
        return result