class Solution:
    def getRow(self, rowIndex: int) -> List[int]:
        rowList = []
        for i in range(rowIndex + 1):
            rowList.append(1)
            for j in range(i - 1, 0, -1):
                rowList[j] += rowList[j - 1]
        return rowList