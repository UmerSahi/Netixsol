# -*- coding: utf-8 -*-git

def mean(numbers):
  return sum(numbers)/len(numbers)

def median(numbers):
  sortingnumbers= sorted(numbers)
  n = len(sortingnumbers)
  mid = n // 2
  if n % 2 == 0:
    return (sortingnumbers[mid - 1] + sortingnumbers[mid]) / 2
  else:
    return sortingnumbers[mid]

def mode(numbers):
  counts = {}
  for number in numbers:
    if number in counts:
      counts[number] += 1
    else:
      counts[number] = 1
  max_count = max(counts.values())
  modes = [number for number, count in counts.items() if count == max_count]
  return modes

def main():
  user_input = input("Enter a list of numbers separated by spaces: ")
  numbers = [float(x) for x in user_input.split()]

  if not numbers:
    print("No numbers entered.")
    return

  mean_value = mean(numbers)
  median_value = median(numbers)
  mode_values = mode(numbers)
  print(f"Mean: {mean_value}")
  print(f"Median: {median_value}")
  print(f"Mode: {', '.join(map(str, mode_values))}")
  print("Min" + str(min(numbers)))
  print("Max" + str(max(numbers)))

if __name__ == "__main__":
  main()

