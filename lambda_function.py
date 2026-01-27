######
# Welcome to Pac Watch! This is a bot that tweets out the most recent 
# independent expenditures, aka PAC contributions that are not expressly
# on behalf of a particular candidate, donated to candidates running for 
# the US Congress or Presidency. It pulls contributions from OpenFEC using the 
# independent expenditures endpoint, tabulates the amount donated by a given
# PAC to a given candidate over the current election cycle, and tweets out 
# major information to the @PAC_watch twitter account. 


###=============================================================================
# Imports

import datetime
import httplib2
import json
from json.decoder import JSONDecodeError
import os
import pandas as pd
import tweepy
from time import sleep


###=============================================================================
# Function to set up client needed for twitter calls
# Returns the client object

def get_twitter_client():
	
	consumer_key = os.getenv("TWT_CONSUMER_KEY")
	consumer_secret = os.getenv("TWT_CONSUMER_SECRET")
	access_token = os.getenv("TWT_ACCESS_TOKEN")
	access_secret = os.getenv("TWT_ACCESS_SECRET")
	client = tweepy.Client(consumer_key=consumer_key, 
						consumer_secret=consumer_secret, 
						access_token=access_token, 
						access_token_secret=access_secret)
	return client


###=============================================================================
# Function to safely pull data from a website
# attempt to call requests.get on a url
# Returns the web content of the url, or None in case of any request error

def get_check_errors(url, headers={}):
  
	# Set up GET
	http = httplib2.Http(".cache")
	headers['User-Agent'] = 'Mozilla/5.0'
  
	# Attempt GET
	resp, content = http.request(url, headers=headers)
  
	# Check for errror during GET
	if not resp.get('status') == '200':
		print("ERROR: failed to pull from URL " + url)
		print("Failed with error " + resp.get('status'))
		return None
  
  	# Try to decode JSON content
	try:
		data = json.loads(content)
	except JSONDecodeError:
		print("ERROR: could not decode JSON from URL " + url)
		print(content)
		data = None
	
  	# Return pulled data
	return data


###=============================================================================
# Function to safely pull json data from a website
# Returns the json, or None in case of a request or json decoding error

def get_json(url, args={}, n_tries=10, wait_time=1):

	data = None

	# total of `n_tries` attempts to get data
	for _ in range(n_tries):
		data = get_check_errors(url, args)
		if data is not None:
			break
		sleep(wait_time)
		
	# Return pulled data
	return data


###=============================================================================
# Function that returns info on a candidate from OpenFEC
# Used to find their candidate_id when missing

def query_candidate(firstname, lastname, query_args={}, n_tries=10, wait_time=1):

    # Needed components of query URL
    api_key = os.getenv("OPENFEC_API_KEY")

	# Final query URL
    query_url = ("https://api.open.fec.gov/v1/candidates/search/"
				f"?api_key={api_key}"
				f"&name={firstname}%20{lastname}"
				)
	
	# Call to pull data
    data = get_json(query_url, query_args, n_tries=n_tries, wait_time=wait_time)

	# Return data in json format
    return data


###=============================================================================
# Function that returns latest contributions from OpenFEC

def query_independent_expenditures(last_date=None, last_index=None, 
								   last_expenditure_amount=None, candidate_id=None, 
								   committee_id=None, cycle=None, min_amount=100, 
								   query_args={}, n_tries=10, wait_time=1):

	# Needed components of query URL
	api_key = os.getenv("OPENFEC_API_KEY")
	last_date_str = "" if last_date is None else f"&min_filing_date={last_date}"
	last_index_str = "" if last_index is None else f"&last_index={last_index}"
	last_expenditure_amount_str = "" if last_expenditure_amount is None else \
		f"&last_expenditure_amount={last_expenditure_amount}"
	candidate_id_str = "" if candidate_id is None else f"&candidate_id={candidate_id}"
	committee_id_str = "" if committee_id is None else f"&committee_id={committee_id}"
	cycle_str = "" if cycle is None else f"&cycle={cycle}"
	min_amount_str = "" if min_amount is None else f"&min_amount={min_amount}"

	# Final query URL
	query_url = ("https://api.open.fec.gov/v1/schedules/schedule_e/"
				f"?api_key={api_key}"
				"&sort=expenditure_amount"
				f"{last_date_str}"
				f"{last_index_str}"
				f"{last_expenditure_amount_str}"
				f"{candidate_id_str}"
				f"{committee_id_str}"
				f"{cycle_str}"
				f"{min_amount_str}")
	
	# Call to pull data
	data = get_json(query_url, query_args, n_tries=n_tries, wait_time=wait_time)

	# Return data in json format
	return data
  
  
###=============================================================================
# Function that returns all new contributions from OpenFEC 
# that were filed past the supplied last_date

def get_independent_expenditure_df(last_date=None, candidate_id=None, 
								   committee_id=None, cycle=None, min_amount=100, 
								   query_args={}, n_tries=10, wait_time=1):

	# Initialize pagination info
	last_index = None
	last_expenditure_amount = None

	# Initialize dataframe to hold all latest data
	results_df = pd.DataFrame()

	# Get latest expenditures page by page
	while True:

		# Query latest expenditures page
		latest_json = query_independent_expenditures(
			last_date=last_date, 
            last_index=last_index, 
            last_expenditure_amount=last_expenditure_amount, 
            candidate_id=candidate_id, 
            committee_id=committee_id,
            cycle=cycle,
            min_amount=min_amount,
            query_args=query_args, 
            n_tries=n_tries, 
            wait_time=wait_time
		)
		
		# Check for correct pulling of data
		if latest_json is None:
			print("ERROR: exiting loop due to null latest_json")
			break
		if not isinstance(latest_json, dict):
			print("ERROR: exiting loop due to non-dict latest_json")
			break

		# Process latest expenditures page and check for end of data
		page_results_df = pd.json_normalize(latest_json["results"])
		if page_results_df.shape[0] == 0:
			break

		# Append page results to global results dataframe
		results_df = pd.concat([results_df, page_results_df], ignore_index=True)	

		# Collect pagination info for next page
		if not latest_json.get("pagination") or \
			not latest_json["pagination"].get("last_indexes"):
			print("ERROR: exiting loop due to missing pagination info")
			break
		last_index = latest_json["pagination"]["last_indexes"]["last_index"]
		last_expenditure_amount = \
			latest_json["pagination"]["last_indexes"]["last_expenditure_amount"]

	# Return results dataframe
	return results_df


###=============================================================================
# Function that processes the independent expenditure dataframe
# TODO: review some of the assumptions made here

def process_independent_expenditure_df(df):

    # Early exit if df is null or empty
	if df is None or df.shape[0] == 0:
		return df
	
	# Create copy of the input df to avoid warnings
	df = df.copy()

	# List of most important columns to process
	key_columns = [
		"amendment_indicator", "candidate_id", "candidate_first_name", 
		"candidate_last_name", "candidate_party", "candidate_office_district", 
		"candidate_office_state", "committee.committee_id", "committee.cycle", 
		"committee.name", "expenditure_amount", "expenditure_date", 
		"expenditure_description", "election_type", "support_oppose_indicator"
		]

	# Remove duplicates, which are likely erroneous
	if not all(col in df.columns for col in key_columns):
		print("WARNING: not all core columns present for duplicate removal")
	else:
		df = df.drop_duplicates(subset=key_columns, keep='first')

	# Ensure relevant columns are the correct type
	for col in key_columns:
		if col in df.columns:
			if col == "expenditure_amount":
				df[col] = pd.to_numeric(df[col], errors='coerce')
			else:
				df[col] = df[col].astype(str).replace("nan", None)

	# Remove any contributions with old cycles
	if "committee.cycle" in df.columns:
		current_year = datetime.datetime.now().year
		current_cycle = current_year if current_year % 2 == 0 else current_year - 1
		df = df[df["committee.cycle"].astype(float) >= current_cycle]

	# Only consider new filings, no amendments
	if "amendment_indicator" in df.columns:
		df = df[df["amendment_indicator"] == "N"]

	return df


###=============================================================================
# Function that returns the total reported expenditures from a specified PAC
# to a specified candidate over the specified election cycle

def get_total_pac_to_candidate_in_cycle_amount(candidate_id, committee_id, cycle,
											   firstname=None, lastname=None,
											   query_args={}, n_tries=10, 
											   wait_time=1):
	
	# If candidate ID is not given, pull all committee contributions and manually
	# filter using the candidate's name
	if candidate_id is None:

		# If name is not given either, we are stuck and should return 0
		if firstname is None or lastname is None:
			return 0.0
		
		results_df = get_independent_expenditure_df(committee_id=committee_id, 
												cycle=cycle,
												query_args=query_args, 
												n_tries=n_tries, 
												wait_time=wait_time)
		
	# If candidate ID is given, query normally
	else:	
  
		results_df = get_independent_expenditure_df(candidate_id=candidate_id, 
												committee_id=committee_id, 
												cycle=cycle,
												query_args=query_args, 
												n_tries=n_tries, 
												wait_time=wait_time)
	
	if results_df is None or results_df.shape[0] == 0:
		return 0.0	
	
	results_df = process_independent_expenditure_df(results_df)

    # If querying by name, filter results to only those matching the candidate name
	if candidate_id is None:
		results_df = results_df.loc[
			(results_df["candidate_first_name"].str.upper() == firstname.upper()) &
			(results_df["candidate_last_name"].str.upper() == lastname.upper()), :]
	
	return results_df["expenditure_amount"].sum()


###=============================================================================
# Function that constructs the text body of a tweet 
# detailing a single campaign contribution
# 
# Tweets are formatted as follows:
# [PAC] spends $[amount] on [purpose] [for/against] 
# [Candidate first and last name] ([Party]-[District]).
# 
# If the PAC has previously spend money on this candidate in the past 
# `n_months` months, we report that too on a new line:
# They have now spent $[cumulative amount] [for/against] 
# [Candidate lastname] in the past `n_months` month(s).
#
# Returns the tweet body as a string
def get_tweet_body(row, query_total_contribution=False, char_limit=280):
  
		# Extract relevant data from row
	pac = row["committee.name"]
	amount = row["expenditure_amount"]
	purpose = row["expenditure_description"]
	suppopp = row["support_oppose_indicator"]
	firstname = row["candidate_first_name"]
	lastname = row["candidate_last_name"]
	party = row["candidate_party"]
	district = row["candidate_office_district"]
	state = row["candidate_office_state"]
	expenditure_date = row["expenditure_date"]
	filing_date = row["filing_date"]
	election = row["election_type"]

	# Check for absolutely necessary info
	if expenditure_date is None or pac is None or amount is None \
		or firstname is None or lastname is None \
		or suppopp is None or election is None:
		return ""

	# Process PAC name
    # Fix anything that could be interpreted by twitter as a url
	if "." in pac[:-1]:
		pac = pac[:-1].replace(".", " dot ") + pac[-1]
	pac = pac.title()

	# Process contribution amount
	amount_str = f"${amount:,.2f}"
	if amount_str.endswith(".00"):
		amount_str = amount_str[:-3]

	# Process candidate info
	firstname = firstname.title()
	lastname = lastname.title()
	party = "D" if party == "DEM" else ("R" if party == "REP" else "")
	if district is None or district == "" or district == "00":
		district = ""
	candidate_info_string = ""
	if party != "":
		candidate_info_string += party 
	if state is not None and state != "":
		if candidate_info_string != "":
			candidate_info_string += "-"
		candidate_info_string += state
	if district != "":
		if candidate_info_string != "":
			candidate_info_string += district

	# Process other fields
	suppopp = "for" if suppopp == "S" else "against"
	expenditure_date = datetime.datetime.strptime(expenditure_date, "%Y-%m-%d").strftime("%m/%d/%Y")
	filing_date = datetime.datetime.strptime(filing_date, "%Y-%m-%d").strftime("%m/%d/%Y")
	
	# If purpose is given, add it to tweet
	purpose_str = ""
	if purpose is not None and purpose != "":
		purpose = str(purpose)
		purpose = purpose.lower()
		purpose = purpose.replace("tv", "TV").replace("t.v.", "TV")
		purpose_str = f" on {purpose}"

	### Construct tweet body

	# Main tweet
	body = f'{pac} spends {amount_str}{purpose_str} {suppopp} ' + \
			f'{firstname} {lastname} ({candidate_info_string}) {election}.'

	# if tweet is too long, remove purpose note
	if len(body) > char_limit:
		body = f'{pac} spends ${amount_str} {suppopp} {firstname} ' + \
				f'{lastname} ({candidate_info_string}).'
		
	# Optionally add info on the total cumulative contribution 
    # from this PAC to this candidate in this cycle
	if query_total_contribution:
		candidate_id = row["candidate_id"]
		committee_id = row["committee.committee_id"]
		cycle = row["committee.cycle"]
		if committee_id is not None and cycle is not None:

			# Get total contribution, whether or not candidate_id is known
			if candidate_id is None or candidate_id == "" or candidate_id == "None":
				total_contribution = get_total_pac_to_candidate_in_cycle_amount(
					None, committee_id, cycle, firstname=firstname, lastname=lastname)
			else:
				total_contribution = get_total_pac_to_candidate_in_cycle_amount(
					candidate_id, committee_id, cycle)

			if total_contribution > amount:
				total_amount_str = f"${total_contribution:,.2f}"
				if total_amount_str.endswith(".00"):
					total_amount_str = total_amount_str[:-3]
				additional_line = f'\n\nThey have now spent {total_amount_str} ' + \
					f'{suppopp} {lastname} this cycle.'
				if len(body) + len(additional_line) <= char_limit:
					body += additional_line
					
    # Add date at the bottom
	body += f'\n\nDisbursed {expenditure_date}\nFiled {filing_date}'
	
	# Truncate tweet if it's over Twitter's character limit
	# This should probably never happen
	if len(body) > char_limit:
		body = body[:char_limit]

	return body


###=============================================================================
# Function that posts a tweet to @PAC_watch
# On failure, sleeps for `wait_time` secs and tries again up to `n_tries` times
# Returns tweet result object on success or None on failure
def send_tweet(message, client, n_tries=10, wait_time=1):
  
	def try_send_tweet(message, client):
		try:
			tweet_result = client.create_tweet(text=message)
		except Exception as e:
			print(e)
			tweet_result = None
		return tweet_result

	tweet_result = try_send_tweet(message, client)
	while tweet_result is None:
		if n_tries == 0:
			break
		n_tries -= 1
		sleep(wait_time)
		tweet_result = try_send_tweet(message, client)

	return None if tweet_result is None else tweet_result
  

###=============================================================================
# Function that executes the main procedure of the script
def main(min_report_amt=100, report_total_contributions=True,
		 verbose=True, tweet=False, between_tweets_time=15):
  
	# Get start time for program execution
	curr_datetime = datetime.datetime.now()
	if verbose:
		print("Running at " + str(curr_datetime))
	
	# Get twitter client for interactions with Twitter API
	twitter_client = get_twitter_client()

	# Pull all latest expenditures in the past day
	last_date = (curr_datetime - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
	if verbose:
		print(f"Pulling all reports since {last_date}")
	latest_df = get_independent_expenditure_df(
		last_date=last_date, min_amount=min_report_amt)
	if verbose:
		print(f"Pulled {latest_df.shape[0]} total reports")
	latest_df = process_independent_expenditure_df(latest_df)
	if verbose:
		print(f"Processed to {latest_df.shape[0]} total reports after cleaning")

    # Send a tweet for each latest expenditure
	for _, row in latest_df.iterrows():
		tweet_body = get_tweet_body(row, query_total_contribution=report_total_contributions)
		if verbose:
			print(tweet_body)
		if tweet:
			tweet_result = send_tweet(tweet_body, twitter_client)
			if tweet_result is None:
				if verbose:
					print("failed to tweet")
			sleep(between_tweets_time)
	
	return 0
 
 
###=============================================================================
# Lambda start point 

def lambda_handler(event, context):
	main(min_report_amt=100,  report_total_contributions=True, verbose=True, 
	  tweet=True, between_tweets_time=15)
	return {"statusCode": 200} 


###=============================================================================
# FOR TESTING - execution start point

def __main__():
	main()


if __name__ == "__main__":
	__main__()

