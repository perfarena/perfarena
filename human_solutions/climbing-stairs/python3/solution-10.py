def climb(n):
      if n == 1:
          return 1
      if n == 2:
          return 2
      one_before = 1
      two_before = 1
      total = 0
      for i in range(2, n):
          total = one_before + two_before
          two_before = one_before
          one_before = total
      return total