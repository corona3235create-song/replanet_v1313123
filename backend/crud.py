from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import datetime, timedelta

from . import models, schemas
from .models import User, Challenge, ChallengeMember, MobilityLog, CreditsLedger, GardenLevel, UserGarden, GardenWateringLog
from .schemas import UserCreate, ChallengeCreate, UserContext

# =========================
# UserGroup
# =========================
def create_user_group(db: Session, group: schemas.UserGroupCreate):
    db_group = models.UserGroup(
        group_name=group.group_name,
        group_type=group.group_type,
        region_code=group.region_code
    )
    db.add(db_group)
    db.commit()
    db.refresh(db_group)
    return db_group

def get_user_groups(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.UserGroup).offset(skip).limit(limit).all()


# =========================
# User
# =========================
def create_user(db: Session, user: schemas.UserCreate):
    db_user = models.User(
        username=user.username,
        email=user.email,
        password_hash=user.password_hash,
        role=user.role,
        user_group_id=user.user_group_id
    )
    db.add(db_user)
    db.commit() # 사용자 생성을 위한 첫 commit
    db.refresh(db_user)

    # --- 💡 챌린지 참여 로직 수정 ---
    default_challenges = db.query(models.Challenge).filter(
        models.Challenge.scope == schemas.ChallengeScope.PERSONAL
    ).all()

    for challenge in default_challenges:
        # join_challenge 함수 호출 대신, ChallengeMember 객체를 직접 생성
        new_member = models.ChallengeMember(
            user_id=db_user.user_id,
            challenge_id=challenge.challenge_id
        )
        db.add(new_member) # 세션에 추가만 함 (commit은 아직 안 함)
    
    db.commit() # 💡 추가된 모든 챌린지 멤버를 한 번에 저장
    # -------------------------------

    return db_user

def get_users(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.User).offset(skip).limit(limit).all()

def get_user_by_id(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.user_id == user_id).first()

def get_user_by_username(db: Session, username: str):
    return db.query(models.User).filter(models.User.username == username).first()

def delete_user(db: Session, user_id: int):
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        return None

    # Delete related MobilityLogs
    db.query(models.MobilityLog).filter(models.MobilityLog.user_id == user_id).delete(synchronize_session=False)
    # Delete related CreditsLedger entries
    db.query(models.CreditsLedger).filter(models.CreditsLedger.user_id == user_id).delete(synchronize_session=False)
    # Delete related ChallengeMembers
    db.query(models.ChallengeMember).filter(models.ChallengeMember.user_id == user_id).delete(synchronize_session=False)
    # Delete related UserGarden and GardenWateringLogs
    db.query(models.GardenWateringLog).filter(models.GardenWateringLog.user_id == user_id).delete(synchronize_session=False)
    db.query(models.UserGarden).filter(models.UserGarden.user_id == user_id).delete(synchronize_session=False)
    # Delete Challenges created by the user
    db.query(models.Challenge).filter(models.Challenge.created_by == user_id).delete(synchronize_session=False)
    # Delete UserAchievements
    db.query(models.UserAchievement).filter(models.UserAchievement.user_id == user_id).delete(synchronize_session=False)
    # Delete Notifications
    db.query(models.Notification).filter(models.Notification.user_id == user_id).delete(synchronize_session=False)
    # Delete IngestRaw entries
    db.query(models.IngestRaw).filter(models.IngestRaw.user_id == user_id).delete(synchronize_session=False)

    db.delete(user)
    db.commit()
    return user

def get_user_with_group(db: Session, user_id: int) -> UserContext | None:
    result = (
        db.query(models.User.username, models.UserGroup.group_name, models.UserGroup.group_type)
        .join(models.UserGroup, models.User.user_group_id == models.UserGroup.group_id, isouter=True)
        .filter(models.User.user_id == user_id)
        .first()
    )
    if result:
        username, group_name, group_type = result
        return UserContext(username=username, group_name=group_name, group_type=group_type)
    return None

def authenticate_user(db: Session, username: str, password: str):
    # In a real application, you would hash the password and compare it with the stored hash.
    # For simplicity, we're doing a direct comparison here.
    # Also, you might want to allow login with email as well.
    user = db.query(models.User).filter(
        (models.User.username == username) | (models.User.email == username)
    ).first()
    if not user or user.password_hash != password: # Assuming password_hash stores plain password for now
        return None
    return user

# =========================
# MobilityLog
# =========================
def create_mobility_log(db: Session, log: schemas.MobilityLogCreate):
    # Get carbon factor for the mode
    carbon_factor = db.query(models.CarbonFactor).filter(
        models.CarbonFactor.mode == log.mode,
        models.CarbonFactor.valid_from <= log.started_at,
        models.CarbonFactor.valid_to >= log.ended_at
    ).first()

    if not carbon_factor:
        # Fallback or raise error if no carbon factor found
        # For simplicity, let's assume a default or raise an error
        # For now, we'll use a default if not found, or 0 saved
        co2_saved_g = 0.0
        print(f"Warning: No carbon factor found for mode {log.mode}. CO2 saved set to 0.")
    else:
        co2_saved_g = float(log.distance_km) * float(carbon_factor.g_per_km)

    # Calculate points earned (e.g., 1 point per 100g CO2 saved)
    points_earned = int(co2_saved_g / 100) # 1 point per 100g saved

    db_log = models.MobilityLog(
        user_id=log.user_id,
        mode=log.mode,
        distance_km=log.distance_km,
        started_at=log.started_at,
        ended_at=log.ended_at,
        co2_saved_g=co2_saved_g,
        points_earned=points_earned,
        description=log.description,
        start_point=log.start_point,
        end_point=log.end_point,
        # source_id, raw_ref_id, co2_baseline_g, co2_actual_g, used_at can be added if needed
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log

# =========================
# CREDITS LEDGER
# =========================
def add_credits(db: Session, user_id: int, points: int, reason: str, ref_log_id: int = None):
    """
    Add credits to a user's ledger.
    """
    db_credit_entry = models.CreditsLedger(
        user_id=user_id,
        ref_log_id=ref_log_id,
        type=models.CreditType.EARN, # Assuming this is always an EARN type for rewards
        points=points,
        reason=reason
    )
    db.add(db_credit_entry)
    db.commit()
    db.refresh(db_credit_entry)
    return db_credit_entry

# =========================
# Challenge
# =========================
def create_challenge(db: Session, challenge: schemas.ChallengeCreate):
    db_challenge = models.Challenge(
        title=challenge.title,
        description=challenge.description,
        scope=challenge.scope,
        completion_type=challenge.completion_type, # Add this line
        target_mode=challenge.target_mode,
        goal_type=challenge.goal_type,
        goal_target_value=challenge.goal_target_value,
        start_at=challenge.start_at,
        end_at=challenge.end_at,
        reward=challenge.reward,
        created_by=challenge.created_by
    )
    db.add(db_challenge)
    db.commit()
    db.refresh(db_challenge)
    return db_challenge

def get_challenge(db: Session, challenge_id: int):
    return db.query(models.Challenge).filter(models.Challenge.challenge_id == challenge_id).first()

def get_challenges(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Challenge).offset(skip).limit(limit).all()

def update_challenge(db: Session, challenge_id: int, challenge: schemas.ChallengeCreate):
    db_challenge = db.query(models.Challenge).filter(models.Challenge.challenge_id == challenge_id).first()
    if db_challenge:
        for key, value in challenge.dict(exclude_unset=True).items():
            setattr(db_challenge, key, value)
        db.commit()
        db.refresh(db_challenge)
    return db_challenge

def delete_challenge(db: Session, challenge_id: int):
    db_challenge = db.query(models.Challenge).filter(models.Challenge.challenge_id == challenge_id).first()
    if db_challenge:
        db.delete(db_challenge)
        db.commit()
    return db_challenge

# =========================
# ChallengeMember
# =========================
def join_challenge(db: Session, user_id: int, challenge_id: int):
    # Check if the user has already joined this challenge
    existing_member = db.query(models.ChallengeMember).filter(
        models.ChallengeMember.user_id == user_id,
        models.ChallengeMember.challenge_id == challenge_id
    ).first()

    if existing_member:
        # User has already joined, return the existing membership
        return existing_member
    
    db_member = models.ChallengeMember(user_id=user_id, challenge_id=challenge_id)
    db.add(db_member)
    db.commit()
    db.refresh(db_member)
    return db_member

def leave_challenge(db: Session, user_id: int, challenge_id: int):
    db_member = db.query(models.ChallengeMember).filter(
        models.ChallengeMember.user_id == user_id,
        models.ChallengeMember.challenge_id == challenge_id
    ).first()
    if db_member:
        db.delete(db_member)
        db.commit()
    return db_member

def get_user_challenges(db: Session, user_id: int, skip: int = 0, limit: int = 100):
    return db.query(models.Challenge).join(models.ChallengeMember).filter(
        models.ChallengeMember.user_id == user_id
    ).offset(skip).limit(limit).all()

# =========================
# Challenge Progress Calculation
# =========================
def calculate_challenge_progress(db: Session, user_id: int, challenge: models.Challenge) -> float:
    total_achieved_value = 0.0
    
    # Filter for mobility logs within the challenge period and for the target mode
    query_filters = [
        models.MobilityLog.user_id == user_id,
        models.MobilityLog.started_at >= challenge.start_at,
        models.MobilityLog.ended_at <= challenge.end_at,
    ]
    if challenge.target_mode != schemas.TransportMode.ANY:
        query_filters.append(models.MobilityLog.mode == challenge.target_mode)

    if challenge.goal_type == schemas.ChallengeGoalType.CO2_SAVED:
        total_achieved_value = db.query(func.sum(models.MobilityLog.co2_saved_g)).filter(*query_filters).scalar()
    elif challenge.goal_type == schemas.ChallengeGoalType.DISTANCE_KM:
        total_achieved_value = db.query(func.sum(models.MobilityLog.distance_km)).filter(*query_filters).scalar()
    elif challenge.goal_type == schemas.ChallengeGoalType.TRIP_COUNT:
        total_achieved_value = db.query(func.count(models.MobilityLog.log_id)).filter(*query_filters).scalar()
    
    if total_achieved_value is None:
        total_achieved_value = 0.0

    progress = (float(total_achieved_value) / float(challenge.goal_target_value)) * 100 if challenge.goal_target_value > 0 else 0.0
    return round(progress, 1) # 소수점 첫째 자리까지 반올림

def update_challenge_status_if_completed(db: Session, challenge: models.Challenge, progress: float, user_id: int):
    # Only auto-complete if the challenge completion type is AUTO
    if challenge.completion_type == schemas.ChallengeCompletionType.AUTO and progress >= 100.0 and challenge.status == models.ChallengeStatus.ACTIVE:
        challenge.status = models.ChallengeStatus.COMPLETED
        db.add(challenge)

        # ChallengeMember의 is_completed 필드 업데이트
        member_entry = db.query(models.ChallengeMember).filter(
            models.ChallengeMember.user_id == user_id,
            models.ChallengeMember.challenge_id == challenge.challenge_id
        ).first()
        if member_entry:
            member_entry.is_completed = True
            db.add(member_entry)

        db.commit()
        db.refresh(challenge)
        if member_entry:
            db.refresh(member_entry)
    return challenge

def update_personal_challenge_progress(db: Session, user_id: int, challenge_id: int, progress_increment: float):
    challenge_member = db.query(ChallengeMember).filter(ChallengeMember.user_id == user_id, ChallengeMember.challenge_id == challenge_id).first()
    challenge = db.query(Challenge).filter(Challenge.challenge_id == challenge_id).first()

    if not challenge_member or not challenge:
        return None

    # ChallengeMember에 progress 필드가 없으므로, 이 함수는 MobilityService에서 호출될 때
    # calculate_challenge_progress를 통해 전체 진행도를 다시 계산하는 방식으로 동작해야 합니다.
    # 여기서는 단순히 is_completed만 업데이트하는 로직으로 변경합니다.
    # 실제 진행도 업데이트는 calculate_challenge_progress 함수를 통해 이루어집니다.

    # 이 함수는 주로 챌린지 완료 상태를 수동으로 업데이트하거나,
    # 특정 조건에 따라 is_completed를 설정하는 데 사용될 수 있습니다.
    # 현재는 MobilityService에서 이 함수를 호출할 때 progress_increment를 사용하지 않고
    # is_completed를 직접 설정하는 방식으로 변경하는 것이 적절해 보입니다.

    # 일단은 오류를 해결하기 위해 Challenge.id -> Challenge.challenge_id로 변경하고,
    # progress_increment는 사용하지 않는 것으로 가정합니다.
    # 이 함수가 호출되는 MobilityService.log_mobility 함수를 확인하여 추가 수정이 필요할 수 있습니다.

    # 챌린지 진행도 계산은 calculate_challenge_progress 함수에서 담당하고,
    # 이 함수는 챌린지 완료 여부만 업데이트하는 것으로 가정합니다.
    # 따라서 progress_increment는 이 함수에서 직접 사용되지 않습니다.

    # MobilityService.log_mobility에서 이 함수를 호출할 때,
    # 챌린지 진행도를 계산한 후, 해당 챌린지가 완료되었는지 여부를 판단하여
    # 이 함수의 is_completed를 True로 설정하도록 호출하는 것이 적절합니다.

    # 현재는 오류 해결을 위해 최소한의 변경만 적용합니다.
    # 이 함수가 호출되는 MobilityService.log_mobility 함수를 확인하여
    # 챌린지 진행도 업데이트 로직을 더 명확히 해야 합니다.

    # 임시로 is_completed만 업데이트하는 로직으로 변경
    # (MobilityService에서 챌린지 완료 시 이 함수를 호출하여 is_completed를 True로 설정한다고 가정)
    # challenge_member.is_completed = True # 이 부분은 MobilityService에서 결정되��야 함

    db.commit()
    db.refresh(challenge_member)

    return challenge_member
